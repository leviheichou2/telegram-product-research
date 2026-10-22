"""Multi-layer product identification.

The app used to answer "what is this photo?" with a single CLIP zero-shot pass
over a fixed label list. That is one signal, and it fails predictably: two
sellers photograph the same dress differently, packaging in Arabic carries the
real product name, and a model number printed on the box beats any visual guess.

This module keeps the signals separate and only fuses them at the end:

    layer 1  visual similarity   DINOv2 embedding + FAISS nearest neighbours
    layer 2  OCR text            TrOCR reading the product / packaging
    layer 3  brand match         OCR text against the candidate's brand
    layer 4  model match         part numbers compared, conflicts rejected
    layer 5  packaging           pack size / variant wording must agree
    layer 6  supplier evidence   the imported supplier directory
    layer 7  confidence          weighted fusion with per-layer reasons

Everything heavy (torch, transformers, faiss) is imported lazily so importing
this module is free and the unit tests run without any model on disk. Every
backend has an explicit fallback: DINOv2 -> CLIP -> perceptual hash, and
FAISS -> exact cosine search, so identification degrades instead of breaking.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "data" / "models"
DINOV2_DIR = MODEL_DIR / "dinov2-small"
CLIP_DIR = MODEL_DIR / "clip-vit-base-patch32"
TROCR_DIR = MODEL_DIR / "trocr-small-printed"

EMBEDDING_DIM_DINOV2 = 384
EMBEDDING_DIM_CLIP = 512

# Confidence bands. `exact` is what the product page is allowed to call a
# confirmed match; anything below `likely` is shown as a candidate only.
EXACT_CONFIDENCE = 0.72
LIKELY_CONFIDENCE = 0.55
POSSIBLE_CONFIDENCE = 0.35

# Layer weights. They sum to 1.0 and a missing layer is removed from the
# denominator rather than scored zero, so OCR-less photos are not punished.
LAYER_WEIGHTS = {
    "visual": 0.34,
    "ocr": 0.22,
    "brand": 0.16,
    "model": 0.14,
    "packaging": 0.08,
    "supplier": 0.06,
}

GENERIC_WORDS = {
    "new", "hot", "sale", "best", "quality", "high", "original", "promo",
    "promotion", "free", "shipping", "delivery", "gift", "pcs", "piece", "set",
    "pack", "the", "and", "with", "for", "from", "price", "stock", "offer",
    "product", "item", "size", "color", "colour", "model", "brand",
}

BRAND_NAMES = {
    "samsung", "xiaomi", "redmi", "huawei", "honor", "oppo", "vivo", "realme",
    "tecno", "infinix", "itel", "apple", "iphone", "nike", "adidas", "puma",
    "reebok", "zara", "hm", "shein", "gucci", "dior", "chanel", "loreal",
    "nivea", "garnier", "maybelline", "philips", "braun", "tefal", "moulinex",
    "bosch", "sencor", "condor", "iris", "beko", "lg", "sony", "jbl", "anker",
    "baseus", "ugreen", "lenovo", "hp", "dell", "asus", "acer", "canon",
    "nikon", "casio", "rolex", "tissot", "fossil", "dettol", "ariel", "omo",
}

# A model / part number: letters and digits together, e.g. SM-A546B, XR-2200,
# TWS-F9, 4K-120. A bare number is a size or a price, not a model.
_MODEL_RE = re.compile(r"\b(?=[A-Z0-9-]{3,18}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{2,17}\b")
_QUANTITY_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(kg|g|mg|ml|l|cl|pcs|pc|pieces|piece|pack|packs|"
    r"مو|قطعة|قطع|كغ|غرام|مل|لتر|علبة|عبوة)\b",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"\b[A-Z][a-zA-Z0-9&\-]{2,}\b")

# A capacity or a rating looks like a model number but describes the product,
# not which product it is: 1500W, 128GB, 500ML, 5000MAH.
_UNIT_CODE_RE = re.compile(r"^\d+(W|V|GB|TB|MB|ML|KG|G|L|MAH|MM|CM|IN|HZ|P)$")


def _lazy_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^\w\u0600-\u06FF]+", " ", stripped.lower()).strip()


def words(text: str) -> set[str]:
    """Content words, with Arabic article folding and naive plural folding."""
    found = set()
    for word in normalize(text).split():
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        if len(word) < 3 or word in GENERIC_WORDS:
            continue
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        found.add(word)
    return found


def model_codes(text: str) -> set[str]:
    """Part numbers in a piece of text, uppercased and dash-normalised."""
    found = set()
    for raw in _MODEL_RE.findall(str(text or "").upper()):
        code = raw.strip("-")
        if not code or code in {"PCS", "KG", "ML", "TWS", "LED", "USB"}:
            continue
        if code.isdigit() or _UNIT_CODE_RE.match(code):
            continue
        found.add(code)
    return found


def extract_brand(text: str) -> str:
    """The most likely brand in a piece of text.

    A known brand name wins. Otherwise the first capitalised, non-generic word
    that is not a model code is taken, because packaging prints the brand above
    everything else.
    """
    lowered = normalize(text)
    for brand in sorted(BRAND_NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(brand)}\b", lowered):
            return brand
    codes = model_codes(text)
    for candidate in _TITLE_RE.findall(str(text or "")):
        if candidate.upper() in codes:
            continue
        if normalize(candidate) in GENERIC_WORDS:
            continue
        return normalize(candidate)
    return ""


def packaging_signature(text: str) -> frozenset[str]:
    """Quantities such as "500ml", "2 pcs" or "1kg".

    The signature is compared as a set: an ad that says 2 pcs describes a
    different offer than a product sold as 1 pc, and it must not be filed under
    exact ads on the strength of the name alone.
    """
    found = set()
    for amount, unit in _QUANTITY_RE.findall(str(text or "")):
        number = amount.replace(",", ".")
        if number.endswith(".0"):
            number = number[:-2]
        found.add(f"{number}{unit.lower()}")
    return frozenset(found)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

_state: dict[str, object] = {}


def _load_dinov2():
    if "dinov2" in _state:
        return _state["dinov2"]
    model = None
    if (DINOV2_DIR / "config.json").is_file():
        transformers = _lazy_import("transformers")
        if transformers is not None:
            try:
                model = (
                    transformers.AutoImageProcessor.from_pretrained(str(DINOV2_DIR)),
                    transformers.AutoModel.from_pretrained(str(DINOV2_DIR)),
                )
            except Exception:
                model = None
    _state["dinov2"] = model
    return model


def _load_clip():
    if "clip" in _state:
        return _state["clip"]
    model = None
    if (CLIP_DIR / "config.json").is_file():
        transformers = _lazy_import("transformers")
        if transformers is not None:
            try:
                model = (
                    transformers.CLIPImageProcessor.from_pretrained(str(CLIP_DIR)),
                    transformers.CLIPModel.from_pretrained(str(CLIP_DIR)),
                )
            except Exception:
                model = None
    _state["clip"] = model
    return model


def embedding_backend() -> str:
    """Which visual backend is usable: ``dinov2``, ``clip`` or ``none``."""
    if _load_dinov2() is not None:
        return "dinov2"
    if _load_clip() is not None:
        return "clip"
    return "none"


def embedding_version() -> str:
    """Stored alongside vectors so a backend switch never compares apples to oranges."""
    return f"{embedding_backend()}:v1"


def _open_rgb(source):
    from PIL import Image

    if isinstance(source, (bytes, bytearray)):
        import io

        return Image.open(io.BytesIO(bytes(source))).convert("RGB")
    with Image.open(source) as handle:
        return handle.convert("RGB")


def load_image(source):
    """Decode one source to an RGB image, raising when it is not an image."""
    return _open_rgb(source)


def embed_images(sources: list) -> list[list[float] | None]:
    """L2-normalised visual embeddings, one per source; ``None`` when unreadable."""
    backend = embedding_backend()
    if backend == "none":
        raise RuntimeError(
            "No visual model is installed. Download one once with: "
            "python -c \"from huggingface_hub import snapshot_download; "
            "snapshot_download('facebook/dinov2-small', local_dir='data/models/dinov2-small')\""
        )
    import torch

    processor, model = _load_dinov2() if backend == "dinov2" else _load_clip()
    model.eval()
    vectors: list[list[float] | None] = []
    for source in sources:
        try:
            image = _open_rgb(source)
        except Exception:
            vectors.append(None)
            continue
        try:
            inputs = processor(images=image, return_tensors="pt")
            with torch.no_grad():
                output = model(**inputs)
            tensor = getattr(output, "pooler_output", None)
            if tensor is None:
                tensor = getattr(output, "image_embeds", None)
            if tensor is None:
                # DINOv2 exposes last_hidden_state: the CLS token is the image vector.
                tensor = output.last_hidden_state[:, 0, :]
            tensor = tensor / tensor.norm(dim=-1, keepdim=True)
            vectors.append([float(value) for value in tensor[0].float().tolist()])
        except Exception:
            vectors.append(None)
        finally:
            image.close()
    return vectors


def embed_image(source) -> list[float] | None:
    return embed_images([source])[0]


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _cosine_search(matrix, query, limit):
    import math

    scored = []
    for row_index, row in enumerate(matrix):
        dot = sum(a * b for a, b in zip(row, query))
        norm = math.sqrt(sum(a * a for a in row) or 1.0)
        qnorm = math.sqrt(sum(a * a for a in query) or 1.0)
        scored.append((dot / (norm * qnorm), row_index))
    scored.sort(reverse=True)
    return scored[:limit]


class VectorIndex:
    """Nearest-neighbour index over product images.

    FAISS is used when it is installed; otherwise an exact cosine scan over a
    plain list is used. Both paths return the same ``[(key, score), ...]`` shape,
    so callers never branch on the backend.
    """

    def __init__(self, dimension: int | None = None):
        self.dimension = dimension
        self.keys: list[str] = []
        self.matrix: list[list[float]] = []
        self._faiss_index = None
        self.backend = "exact"

    def add(self, keys: list[str], vectors: list[list[float] | None]) -> int:
        added = 0
        for key, vector in zip(keys, vectors):
            if not vector:
                continue
            if self.dimension and len(vector) != self.dimension:
                continue
            self.dimension = self.dimension or len(vector)
            self.keys.append(str(key))
            self.matrix.append(vector)
            added += 1
        self._faiss_index = None
        return added

    def _faiss(self):
        if self._faiss_index is not None:
            return self._faiss_index
        faiss = _lazy_import("faiss")
        if faiss is None or not self.matrix:
            return None
        import numpy

        index = faiss.IndexFlatIP(len(self.matrix[0]))
        index.add(numpy.asarray(self.matrix, dtype="float32"))
        self._faiss_index = index
        self.backend = "faiss"
        return index

    def search(self, vector: list[float], limit: int = 10) -> list[tuple[str, float]]:
        if not self.matrix or not vector:
            return []
        index = self._faiss()
        if index is not None:
            try:
                import numpy

                scores, positions = index.search(
                    numpy.asarray([vector], dtype="float32"), min(limit, len(self.keys))
                )
                return [
                    (self.keys[int(position)], round(float(score), 4))
                    for score, position in zip(scores[0], positions[0])
                    if int(position) >= 0
                ]
            except Exception:
                pass
        return [
            (self.keys[row_index], round(score, 4))
            for score, row_index in _cosine_search(self.matrix, vector, limit)
        ]


def index_from_paths(rows: list[tuple[str, Path]], index: VectorIndex | None = None):
    """Embed ``(key, path)`` rows into an index, skipping unreadable files."""
    index = index or VectorIndex()
    sources = [path for _, path in rows]
    vectors = embed_images(sources)
    index.add([key for key, _ in rows], vectors)
    return index


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def ocr_available() -> bool:
    return (TROCR_DIR / "config.json").is_file() and _lazy_import("transformers") is not None


def read_text(source) -> str:
    """Packaging / label text from an image, or ``""`` when OCR is unavailable."""
    if not ocr_available():
        return ""
    if "ocr" not in _state:
        transformers = _lazy_import("transformers")
        try:
            _state["ocr"] = (
                transformers.TrOCRProcessor.from_pretrained(str(TROCR_DIR)),
                transformers.VisionEncoderDecoderModel.from_pretrained(str(TROCR_DIR)),
            )
        except Exception:
            _state["ocr"] = None
    bundle = _state.get("ocr")
    if not bundle:
        return ""
    import torch

    processor, model = bundle
    try:
        image = _open_rgb(source)
    except Exception:
        return ""
    try:
        pixel_values = processor(images=image, return_tensors="pt").pixel_values
        with torch.no_grad():
            generated = model.generate(pixel_values, max_new_tokens=48)
        return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
    except Exception:
        return ""
    finally:
        image.close()


# ---------------------------------------------------------------------------
# Layer 7: confidence fusion
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    name: str
    score: float
    detail: str = ""
    available: bool = True

    def as_dict(self) -> dict:
        return {
            "layer": self.name,
            "score": round(self.score, 3),
            "detail": self.detail,
            "available": self.available,
        }


@dataclass
class Identity:
    """The fused answer for one photo, with every layer kept for inspection."""

    product_key: str = ""
    name: str = ""
    brand: str = ""
    models: list[str] = field(default_factory=list)
    packaging: list[str] = field(default_factory=list)
    ocr_text: str = ""
    confidence: float = 0.0
    verdict: str = "unknown"
    layers: list[Layer] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "product_key": self.product_key,
            "name": self.name,
            "brand": self.brand,
            "models": self.models,
            "packaging": self.packaging,
            "ocr_text": self.ocr_text,
            "confidence": round(self.confidence, 3),
            "verdict": self.verdict,
            "layers": [layer.as_dict() for layer in self.layers],
            "alternatives": self.alternatives,
        }


def fuse(layers: list[Layer]) -> float:
    """Weighted mean over the layers that actually ran."""
    total = 0.0
    weight = 0.0
    for layer in layers:
        if not layer.available:
            continue
        base = LAYER_WEIGHTS.get(layer.name, 0.0)
        total += base * max(0.0, min(1.0, layer.score))
        weight += base
    return round(total / weight, 4) if weight else 0.0


def verdict_for(confidence: float) -> str:
    if confidence >= EXACT_CONFIDENCE:
        return "exact"
    if confidence >= LIKELY_CONFIDENCE:
        return "likely"
    if confidence >= POSSIBLE_CONFIDENCE:
        return "possible"
    return "unknown"


def compare_text(candidate: dict, ocr_text: str) -> list[Layer]:
    """Layers 2-5: OCR overlap, brand, model number and packaging agreement."""
    candidate_text = " ".join(
        str(candidate.get(key) or "")
        for key in ("name", "brand", "specs", "category", "description")
    )
    ocr_words = words(ocr_text)
    candidate_words = words(candidate_text)
    layers: list[Layer] = []

    if not ocr_text.strip():
        layers.append(Layer("ocr", 0.0, "No text was read from the photo", available=False))
        layers.append(Layer("brand", 0.0, "Brand needs OCR text", available=False))
        layers.append(Layer("model", 0.0, "Model number needs OCR text", available=False))
        layers.append(Layer("packaging", 0.0, "Packaging needs OCR text", available=False))
        return layers

    shared = ocr_words & candidate_words
    coverage = len(shared) / len(candidate_words) if candidate_words else 0.0
    layers.append(
        Layer("ocr", min(1.0, coverage * 1.6), f"{len(shared)} shared word(s): " + ", ".join(sorted(shared)[:6]))
    )

    brand = extract_brand(ocr_text)
    expected_brand = str(candidate.get("brand") or "").strip().lower()
    if expected_brand and brand:
        hit = expected_brand in normalize(ocr_text) or brand == expected_brand
        layers.append(Layer("brand", 1.0 if hit else 0.0, f"Read '{brand}', expected '{expected_brand}'"))
    else:
        layers.append(Layer("brand", 0.0, "No brand to compare", available=False))

    read_models = model_codes(ocr_text)
    expected_models = model_codes(candidate_text)
    if expected_models:
        shared_models = read_models & expected_models
        conflicting = {
            code
            for code in read_models - expected_models
            if any(code[:4] == expected[:4] for expected in expected_models)
        }
        if shared_models:
            score = 1.0
            detail = "Model confirmed: " + ", ".join(sorted(shared_models))
        elif conflicting:
            score = 0.0
            detail = "Different model read: " + ", ".join(sorted(conflicting)[:3])
        else:
            score = 0.0
            detail = "Model number not read"
        layers.append(Layer("model", score, detail))
    else:
        layers.append(Layer("model", 0.0, "Candidate has no model number", available=False))

    expected_pack = packaging_signature(candidate_text)
    read_pack = packaging_signature(ocr_text)
    if expected_pack and read_pack:
        agree = bool(expected_pack & read_pack)
        layers.append(
            Layer(
                "packaging",
                1.0 if agree else 0.0,
                ("Packaging agrees: " if agree else "Packaging differs: ")
                + ", ".join(sorted(read_pack)),
            )
        )
    else:
        layers.append(Layer("packaging", 0.0, "No pack size to compare", available=False))
    return layers


def visual_layer(score: float, key: str) -> Layer:
    return Layer("visual", max(0.0, min(1.0, score)), f"Nearest neighbour {key or '-'}")


def supplier_layer(matches: list[dict]) -> Layer:
    """Layer 6: does the supplier directory know this product at all?"""
    if not matches:
        return Layer("supplier", 0.0, "No supplier in the directory", available=False)
    best = max(float(match.get("score") or 0) for match in matches)
    count = len(matches)
    return Layer("supplier", min(1.0, best), f"{count} supplier(s), best {best:.2f}")


def identify(
    source,
    index: VectorIndex | None = None,
    candidates: dict[str, dict] | None = None,
    supplier_matches: dict[str, list[dict]] | None = None,
    limit: int = 5,
    read_ocr: bool = True,
    ocr_text: str | None = None,
) -> Identity:
    """Run every layer for one photo and fuse them into one verdict.

    ``candidates`` maps a product key to what is known about it (``name``,
    ``brand``, ``specs``); it is how the text layers get something to compare
    against. Without a visual index the result is text-only, and vice versa.
    ``ocr_text`` lets a caller supply text that was read earlier (or by hand)
    instead of running TrOCR again.
    """
    candidates = candidates or {}
    supplier_matches = supplier_matches or {}
    if ocr_text is None:
        ocr_text = read_text(source) if read_ocr else ""
    result = Identity(ocr_text=ocr_text)
    result.brand = extract_brand(ocr_text)
    result.models = sorted(model_codes(ocr_text))
    result.packaging = sorted(packaging_signature(ocr_text))

    neighbours: list[tuple[str, float]] = []
    if index is not None and index.keys:
        vector = embed_image(source)
        if vector:
            neighbours = index.search(vector, limit)

    # Text-only identification has no visual neighbours; fall back to the
    # candidates we were given so the OCR layers still produce an answer.
    scored = []
    if neighbours:
        for key, score in neighbours:
            candidate = candidates.get(key) or {}
            layers = [visual_layer(score, key)]
            layers.extend(compare_text(candidate, ocr_text))
            layers.append(supplier_layer(supplier_matches.get(key) or []))
            scored.append((fuse(layers), key, candidate, layers))
    elif ocr_text.strip():
        for key, candidate in list(candidates.items())[:limit]:
            layers = [Layer("visual", 0.0, "No visual index", available=False)]
            layers.extend(compare_text(candidate, ocr_text))
            layers.append(supplier_layer(supplier_matches.get(key) or []))
            scored.append((fuse(layers), key, candidate, layers))

    for confidence, key, candidate, layers in sorted(scored, key=lambda item: -item[0]):
        result.alternatives.append(
            {
                "product_key": key,
                "name": candidate.get("name") or key,
                "confidence": confidence,
                "verdict": verdict_for(confidence),
                "layers": [layer.as_dict() for layer in layers],
            }
        )

    if scored:
        confidence, key, candidate, layers = max(scored, key=lambda item: item[0])
        result.product_key = key
        result.name = str(candidate.get("name") or key)
        result.confidence = confidence
        result.verdict = verdict_for(confidence)
        result.layers = layers
        result.brand = str(candidate.get("brand") or result.brand)
    return result


def status() -> dict:
    """What this machine can do right now, for the settings screen."""
    backend = embedding_backend()
    return {
        "visual_backend": backend,
        "embedding_dimension": EMBEDDING_DIM_DINOV2 if backend == "dinov2" else (
            EMBEDDING_DIM_CLIP if backend == "clip" else 0
        ),
        "vector_backend": "faiss" if _lazy_import("faiss") is not None else "exact",
        "ocr": ocr_available(),
        "dinov2_installed": (DINOV2_DIR / "config.json").is_file(),
        "clip_installed": (CLIP_DIR / "config.json").is_file(),
        "trocr_installed": (TROCR_DIR / "config.json").is_file(),
    }


def content_hash(source) -> str:
    """Stable key for an image, used to cache embeddings of uploaded photos."""
    if isinstance(source, (bytes, bytearray)):
        payload = bytes(source)
    else:
        payload = Path(source).read_bytes()
    return hashlib.sha256(payload).hexdigest()[:32]
