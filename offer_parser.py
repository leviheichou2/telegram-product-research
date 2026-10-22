"""Read the product name out of an Arabic/French wholesale offer post.

Algerian wholesale channels (El Eulma "gros") post the same shape of message
over and over::

    🏃 متراطيش لافار 🔊
    ⭐ منتج tapis de bain et de douche antidérapant
    🤏 السعر: 630 دج
    🚚 التوصيل متوفر 69 ولاية

The product is the phrase after ``منتج``; the first line is the shop's own
signature and the rest is price and delivery. A generic "first short line"
reader returns the signature, and a price-only post returns ``85الف``, which is
what made scans look like they never named anything: the name ended up in the
Meta Ad Library query, so the ads came back wrong too.

This module is a small, dependency-free, testable parser that reads the post
layout instead of guessing:

1. lines are cleaned of emoji and decoration,
2. lines that only carry price, availability, delivery, contact details or shop
   boilerplate are dropped,
3. lines that every post of the channel repeats (the shop signature) are dropped
   by frequency, so they can never become a product name,
4. a product marker (``منتج`` / ``produit`` / ``article`` ...) wins, then a
   descriptive Latin-script line, then a descriptive Arabic line.

The parser never invents a name: when nothing survives, ``Offer.name`` is empty
and the caller keeps the post as a review candidate instead of labelling it
"Disponible".
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

__all__ = [
    "Offer",
    "clean_lines",
    "parse_offer",
    "signature_keys",
    "signature_lines",
]


# Emoji, pictographs, arrows, decoration and invisible joiners. Removing them
# first is what turns "⭐ منتج tapis" into a line the marker can be read from.
_DECORATION = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoji blocks
    "\U00002190-\U000021FF"  # arrows
    "\U00002600-\U000027BF"  # misc symbols and dingbats
    "\U00002B00-\U00002BFF"  # more arrows/stars
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F3FB-\U0001F3FF"  # skin tones
    "\U0000200B-\U0000200F"  # zero width and bidi marks
    "\U00002B50"
    "\U000020E3"
    "]"
)

_LINK_OR_HANDLE = re.compile(r"https?://|www\.|\bt\.me/|\bwa\.me/|@[a-z0-9_]{3,}", re.I)

# Price wording in the three languages these channels use.
_PRICE_WORDS = re.compile(
    r"\b(?:prix|price|tarif|promo|promotion|solde|remise)\b|السعر|سعر|دج|د\.ج|\bdzd\b|\bda\b|\bdinars?\b",
    re.I,
)

# Anything that is only a number plus a unit is a price or a dimension.
_NUMBER_ONLY = re.compile(r"^[\d\s.,:/+\-–]*(?:الف|ألف|k|da|dzd|دج|pcs|pi[eè]ces?|pieces?|kg|g|ml|l|cm|mm|m|v|w|mah|watt|volt)?[\d\s.,:/+\-–]*$", re.I)

# Words that describe an offer, never a product.
_NOISE_WORDS = (
    # French / English
    "disponible", "disponibilité", "dispo", "en stock", "stock", "en gros",
    "demi gros", "gros", "grossiste", "livraison", "livrer", "delivery",
    "commande", "commander", "commandez", "whatsapp", "instagram", "facebook",
    "tiktok", "telegram", "contact", "téléphone", "telephone", "phone", "appel",
    "appelez", "renseignement", "informations", "info", "catalogue", "arrivage",
    "nouveau", "nouveauté", "nouveaute", "nouvelle collection", "sourcing",
    "winning product", "wining product", "best seller", "hot sale", "garantie",
    "material", "matière", "matiere", "couleur", "color", "taille", "size",
    "poids", "weight", "quantité", "quantity", "origine", "lot de", "pack de",
    "envoyez", "envoyer", "message", "voir plus", "abonnez", "abonne",
    # Arabic
    "سعر", "السعر", "سعر الجملة", "جملة", "الجملة", "بالجملة", "توصيل",
    "التوصيل", "متوفر", "متوفرة", "غير متوفر", "للطلب", "الطلب", "اطلب",
    "اتصل", "هاتف", "رقم", "واتساب", "انستغرام", "فيسبوك", "تليجرام", "تيليجرام",
    "ماركة", "قياس", "لون", "وزن", "مادة", "جديد", "جديدة", "عرض", "تخفيض",
    "تواصل", "المزيد", "تفاصيل", "معلومات", "كمية", "الكمية", "قطعة", "قطع",
    "الحد الأدنى", "بالتجزئة", "للتجزئة", "طلب", "السعر الجملة", "أسعار",
    "اسعار", "أسعار مغرية", "جودة", "ضمان", "صورة", "صور", "الكمية محدودة",
    "بسم الله", "سلام", "السلام عليكم", "للبيع", "بيع", "vente", "à vendre", "a vendre",
    # price/quantity words that appear next to a number
    "الف", "ألف", "مليون", "ددج", "alf", "mille", "dinar", "dzd",
    # marketing sentences that read like a product line
    "أفضل", "افضل", "بأفضل", "السوق", "دخول", "بضاعة", "بضاعه", "جملة الجديدة",
    # marketing labels that look like a product line
    "wining", "winning", "products", "articles", "collection",
    # the product labels themselves are not product names
    "منتج", "المنتج", "منتوج", "المنتوج", "produit", "product", "article",
    "موديل", "modele", "modèle",
    # size and colour lists are offer detail, not the product
    "المقاسات", "مقاسات", "المقاس", "مقاس", "الألوان", "الوان", "ألوان",
    "اللون", "كميات", "الكميات", "بقية", "دائما", "دائمًا", "وفرنالكم",
    "ورحمة", "تعالى", "بركاته", "لطلب", "راسلنا", "راسل", "للطلب",
)
_NOISE_PATTERN = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(word) for word in _NOISE_WORDS) + r")(?!\w)",
    re.I,
)

# The explicit product marker, in the order the channels use it.
_MARKER = re.compile(
    r"(?:^|[\s:•·|\-–—(])"
    r"(?:منتج|المنتج|منتوج|المنتوج|produit|product|article|موديل|mod[eè]le|model)"
    r"(?![A-Za-z\u0600-\u06FF])"
    r"\s*[:：\-–—]?\s*"
    r"(?P<value>[^|•\n]{2,90})",
    re.I,
)

_BRAND = re.compile(
    r"(?:^|[\s:•·|])(?:ماركة|الماركة|marque|brand|fabricant)\s*[:：\-–—]?\s*(?P<value>[^|•\n]{2,60})",
    re.I,
)

_ARABIC_LETTER = re.compile(r"[\u0600-\u06FF]")
_LATIN_LETTER = re.compile(r"[A-Za-z\u00C0-\u024F]")


@dataclass(frozen=True)
class Offer:
    """What one offer post says about the product it sells."""

    name: str = ""
    confidence: float = 0.0
    brand: str = ""
    description: str = ""
    signal: str = ""
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def named(self) -> bool:
        return bool(self.name)


def clean_line(line):
    """Strip decoration and surrounding punctuation from one line."""
    text = _DECORATION.sub(" ", str(line or ""))
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return text.strip(" \t-–—•·*|:،.,;؛_=~\"'`")


def clean_lines(text):
    """The meaningful lines of a post, decoration removed."""
    lines = []
    for raw in str(text or "").splitlines():
        line = clean_line(raw)
        if line:
            lines.append(line)
    return lines


def _signature_key(line):
    """A comparison key for a line that ignores case, decoration and spacing."""
    text = clean_line(line).casefold()
    text = re.sub(r"[^\w\u0600-\u06FF]+", " ", text)
    return " ".join(text.split())


def signature_keys(text):
    """Every comparison key in one post (used to count repeated lines)."""
    return {key for key in (_signature_key(line) for line in clean_lines(text)) if key}


def signature_lines(texts, minimum=4, share=0.35):
    """The lines a channel repeats in every post: its shop signature.

    A line that appears in at least ``minimum`` posts *and* in ``share`` of the
    posts is boilerplate ("متراطيش لافار" at the top of every Safir Gros post),
    so it can never be the product name. Counting whole posts, not lines, keeps
    a product that was posted three times from being mistaken for a signature.
    """
    texts = [str(text or "") for text in texts]
    posts = len([text for text in texts if text.strip()])
    if posts < minimum:
        return set()
    counts = Counter()
    for text in texts:
        counts.update(signature_keys(text))
    threshold = max(minimum, int(posts * share + 0.999))
    return {key for key, count in counts.items() if count >= threshold}


def _letters_only(text):
    return re.sub(r"[^\w\u0600-\u06FF ]+", " ", str(text or ""))


def _is_noise(line, signature_key_set=(), channel_tokens=frozenset()):
    """True when a line cannot be a product name."""
    text = clean_line(line)
    if not text or len(text) > 90:
        return True
    if _LINK_OR_HANDLE.search(text):
        return True
    if text.startswith("#"):
        return True
    if _PRICE_WORDS.search(text) and re.search(r"\d", text):
        return True
    if _PRICE_WORDS.match(text):
        return True
    if _NUMBER_ONLY.match(text):
        return True
    letters = [character for character in text if character.isalpha()]
    if len(letters) < 3:
        return True
    if _NOISE_PATTERN.fullmatch(text):
        return True
    # A line whose every word is offer boilerplate ("prix disponible en gros")
    # says nothing about the product.
    words = [word for word in _letters_only(text).split() if word]
    if words and all(_NOISE_PATTERN.fullmatch(word) for word in words):
        return True
    # A price line with a filler word attached ("200 الف جملة") has no word that
    # could name a product once the numbers and the boilerplate are removed.
    meaningful = [
        word
        for word in words
        if not _NOISE_PATTERN.fullmatch(word) and not word.isdigit()
    ]
    if not meaningful:
        return True
    if len(meaningful) <= 1 and len(words) >= 3:
        return True
    # One filler word attached to boilerplate ("للبيع بالجملة") still names no
    # product, even in a two-word line.
    if len(meaningful) <= 1 and len(words) >= 2 and _NOISE_PATTERN.search(text):
        return True
    # A marketing sentence ("متوفر من جديد بأفضل سعر في السوق") is mostly
    # boilerplate words with a couple of joiners left over.
    noise_hits = sum(1 for word in words if _NOISE_PATTERN.fullmatch(word))
    if noise_hits >= 2 and noise_hits * 3 >= len(words):
        return True
    key = _signature_key(text)
    if key and key in signature_key_set:
        return True
    if channel_tokens:
        tokens = {token for token in key.split() if token}
        if tokens and len(tokens) <= 4 and tokens <= channel_tokens:
            return True
        if tokens and len(channel_tokens) <= 4 and channel_tokens <= tokens:
            return True
    return False


# A price glued to the end of the name ("Airpods max 205 alf", "Tapis 1200 دج")
# is offer detail, not part of the product name.
_PRICE_TAIL = re.compile(
    r"\s+[\d\s.,]*\d[\d\s.,]*\s*(?:alf|ألف|الف|k|mille|da|dzd|دج|د\.ج|dinars?|rmb|usd|eur|\$|€)\s*$",
    re.I,
)


def _trim_value(value):
    """Tidy a value read after a marker: drop trailing labels and decoration."""
    text = clean_line(value)
    text = re.split(r"\s+[-–—|•]\s+", text)[0]
    text = _PRICE_TAIL.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" \t-–—•·|:،.,;؛")
    # "Poussette garde robe 3 en 1 disponible" -> drop the trailing offer word,
    # "متوفر مسك أسود" -> drop the leading one.
    words = text.split()
    while len(words) > 1 and _NOISE_PATTERN.fullmatch(words[-1]):
        words.pop()
    while len(words) > 1 and _NOISE_PATTERN.fullmatch(words[0]):
        words.pop(0)
    return " ".join(words)


def _latin_phrase(text):
    """The Latin-script phrase inside a mixed Arabic line.

    Suppliers write the product's real name in Latin letters inside an Arabic
    sentence ("للي وصانا على Littmann Classic 3 !!"); that phrase is the name the
    Ad Library can actually be searched for.
    """
    best = ""
    for match in re.finditer(r"[A-Za-z\u00C0-\u024F][\w\u00C0-\u024F'’./+-]*(?:\s+[\w\u00C0-\u024F'’./+-]+)*", text):
        phrase = match.group(0).strip(" .,;:!?-")
        words = phrase.split()
        long_enough = len(words) >= 2 or (len(words) == 1 and len(phrase) >= 5)
        if long_enough and any(len(word) >= 3 for word in words):
            if len(phrase) > len(best):
                best = phrase
    return best


def _looks_descriptive(text):
    """A name needs a noun-ish phrase, not a single filler word."""
    words = [word for word in _letters_only(text).split() if word]
    if not words:
        return False
    if len(words) >= 2:
        return True
    word = words[0]
    return len(word) >= 4 and not _NOISE_PATTERN.fullmatch(word)


def parse_offer(text, channel_title="", signature=None):
    """Name the product one offer post sells.

    Args:
        text: the post body as Telegram returned it.
        channel_title: the channel's own title, so a post that only repeats it
            cannot be mistaken for a product name.
        signature: the ``signature_lines`` set of the channel, or any collection
            of comparison keys.

    Returns:
        ``Offer`` with the best name found (``""`` when the post does not name
        the product), its confidence, the brand when the post states one, the
        description (the post without the name line) and the signal that won.
    """
    lines = clean_lines(text)
    if not lines:
        return Offer()
    signature = set(signature or ())
    channel_key = _signature_key(channel_title)
    channel_tokens = frozenset(channel_key.split())
    body = "\n".join(lines)

    def acceptable(value):
        value = _trim_value(value)
        if not value:
            return ""
        if _is_noise(value, signature, channel_tokens):
            return ""
        if not _looks_descriptive(value):
            return ""
        return value

    candidates = []
    for index, line in enumerate(lines):
        match = _MARKER.search(line)
        if not match:
            continue
        value = acceptable(match.group("value"))
        if not value:
            # "منتج للي وصانا على Littmann Classic 3" names the product in Latin
            # letters inside the Arabic sentence.
            value = acceptable(_latin_phrase(match.group("value")))
        if value:
            # A marker is the strongest signal these posts carry: the seller
            # wrote "منتج" precisely to introduce the product.
            return _offer(
                value,
                0.9,
                lines,
                body,
                "marker",
                channel_title,
            )

    # No marker: prefer a descriptive Latin-script line (product names in these
    # channels are French or English even when the post is Arabic), then Arabic.
    best_latin = ""
    best_latin_confidence = 0.0
    best_arabic = ""
    best_arabic_confidence = 0.0
    for line in lines:
        value = acceptable(line)
        if not value:
            continue
        letters = [character for character in value if character.isalpha()]
        if not letters:
            continue
        latin = len(_LATIN_LETTER.findall(value)) / len(letters)
        if latin >= 0.7:
            if not best_latin:
                best_latin = value
                best_latin_confidence = 0.7 if len(value.split()) >= 2 else 0.5
        elif _LATIN_LETTER.search(value) and not best_latin:
            # Mixed line: the Latin phrase inside it is the searchable name.
            phrase = acceptable(_latin_phrase(value))
            if phrase and len(phrase.split()) >= 2:
                best_latin = phrase
                best_latin_confidence = 0.65
        if _ARABIC_LETTER.search(value) and not best_arabic:
            best_arabic = value
            best_arabic_confidence = 0.55 if len(value.split()) >= 2 else 0.4
    if best_latin:
        candidates.append((best_latin, best_latin_confidence, "latin line"))
    if best_arabic:
        candidates.append((best_arabic, best_arabic_confidence, "arabic line"))
    if not candidates:
        return Offer()
    name, confidence, signal = candidates[0]
    return _offer(name, confidence, lines, body, signal, channel_title)


def _offer(name, confidence, lines, body, signal, channel_title):
    """Build the result: name plus the brand and the description around it."""
    brand_match = _BRAND.search(body)
    brand = ""
    if brand_match:
        brand = _trim_value(brand_match.group("value"))
        if not _looks_descriptive(brand):
            brand = ""
    description_lines = []
    for line in lines:
        if name and _signature_key(name) and _signature_key(name) in _signature_key(line):
            continue
        if line.strip() == channel_title.strip():
            continue
        description_lines.append(line)
    return Offer(
        name=name,
        confidence=round(confidence, 3),
        brand=brand,
        description="\n".join(description_lines)[:3000],
        signal=signal,
        signals=(signal,),
    )
