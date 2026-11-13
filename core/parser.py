"""Lossless-aware subtitle parser, language detector, and multi-format exporter."""

import html
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pysubs2

from core.models import (
    ExportTrackMode,
    SubtitleCue,
    SubtitleDocument,
    SubtitleFormat,
)

SUPPORTED_INPUT_FORMATS = ("srt", "ass", "ssa", "vtt")
SUPPORTED_OUTPUT_FORMATS = ("srt", "ass", "ssa", "vtt", "txt")

_SRT_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*(?:(\d+)\s*\n)?\s*"
    r"(\d{1,3}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,3}:\d{2}:\d{2}[,.]\d{3})(?:[ \t]+[^\n]*)?\n"
    r"([\s\S]*?)(?=\n{2,}\s*(?:\d+\s*\n\s*)?\d{1,3}:\d{2}:\d{2}[,.]\d{3}\s*-->|\Z)",
    re.MULTILINE,
)
_VTT_CUE_RE = re.compile(
    r"^(?:([^\n]+)\n)?"
    r"((?:\d{1,3}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*((?:\d{1,3}:)?\d{2}:\d{2}\.\d{3})(?:[ \t]+([^\n]*))?\n"
    r"([\s\S]+)$"
)
_ASS_OVERRIDE_RE = re.compile(r"\{[^{}]*\}")
_LEADING_ASS_TAGS_RE = re.compile(r"^(?:\{[^{}]*\})+")
_HTML_TAG_RE = re.compile(r"</?(?:b|i|u|s|font|span|c|v|ruby|rt|lang)(?:\s[^>]*)?>", re.IGNORECASE)
_VTT_VOICE_RE = re.compile(r"^<v(?:\.[^\s>]+)?\s+([^>]+)>([\s\S]*?)(?:</v>)?$", re.IGNORECASE)


class SubtitleParseError(ValueError):
    """Raised when a subtitle file is unreadable or contains no valid cues."""


def decode_subtitle_bytes(data: bytes, encoding: str = "auto") -> Tuple[str, str]:
    """Decode raw bytes with automatic multi-encoding detection for CJK & Western subtitles."""
    if not data:
        raise SubtitleParseError("字幕文件为空。")

    if encoding and encoding != "auto":
        try:
            text = data.decode(encoding, errors="strict")
            return _normalize_newlines(text.lstrip("\ufeff")), encoding
        except UnicodeError as exc:
            raise SubtitleParseError(f"无法按指定编码 {encoding} 解码字幕文件：{exc}") from exc

    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="strict")
        return _normalize_newlines(text.lstrip("\ufeff")), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig", errors="strict")
        return _normalize_newlines(text), "utf-8-sig"

    for candidate in ("utf-8", "gb18030", "big5", "shift_jis", "cp1252"):
        try:
            text = data.decode(candidate, errors="strict")
            if "\x00" in text:
                continue
            return _normalize_newlines(text.lstrip("\ufeff")), candidate
        except UnicodeError:
            continue

    text = data.decode("utf-8", errors="replace")
    return _normalize_newlines(text.lstrip("\ufeff")), "utf-8"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_timestamp_ms(ts: str) -> int:
    """Parse SRT/VTT/ASS timestamp into integer milliseconds."""
    cleaned = ts.strip().replace(",", ".")
    parts = cleaned.split(":")
    if len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        sec_part = parts[1]
    elif len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        sec_part = parts[2]
    else:
        raise SubtitleParseError(f"无效的时间戳格式：{ts}")

    if "." in sec_part:
        sec_str, frac_str = sec_part.split(".", 1)
        seconds = int(sec_str)
        frac_str = (frac_str + "000")[:3]
        millis = int(frac_str)
    else:
        seconds = int(sec_part)
        millis = 0

    return ((hours * 3600) + (minutes * 60) + seconds) * 1000 + millis


def format_timestamp_srt(ms: int, sep: str = ",") -> str:
    ms = max(0, int(round(ms)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def format_timestamp_ass(ms: int) -> str:
    ms = max(0, int(round(ms)))
    cs = (ms + 5) // 10
    hours, rem = divmod(cs, 360_000)
    minutes, rem = divmod(rem, 6000)
    seconds, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def detect_subtitle_language(cues: List[SubtitleCue]) -> str:
    """Heuristic language detector across CJK, Japanese, Korean, Russian, and European scripts."""
    sample = " ".join(c.source_text for c in cues[:80] if not c.is_special)[:4000]
    if not sample.strip():
        return "en"

    hiragana_katakana = len(re.findall(r"[\u3040-\u30ff]", sample))
    hangul = len(re.findall(r"[\uac00-\ud7af]", sample))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    cyrillic = len(re.findall(r"[\u0400-\u04ff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))

    if hiragana_katakana >= 3:
        return "ja"
    if hangul >= 3:
        return "ko"
    if cjk >= 4 and cjk >= latin * 0.25:
        return "zh"
    if cyrillic >= 5:
        return "ru"

    lower = f" {sample.lower()} "
    lang_markers = {
        "fr": (" le ", " la ", " les ", " des ", " est ", " pas ", " vous ", " nous ", " c'est "),
        "es": (" el ", " la ", " los ", " las ", " que ", " por ", " para ", " como ", " pero "),
        "de": (" der ", " die ", " das ", " und ", " ich ", " nicht ", " ist ", " wir ", " sie "),
    }
    best_lang, best_hits = "en", 0
    for code, words in lang_markers.items():
        hits = sum(lower.count(w) for w in words)
        if hits > best_hits and hits >= 3:
            best_lang, best_hits = code, hits
    return best_lang


def _clean_html_cue_text(raw: str) -> Tuple[str, str]:
    speaker = ""
    stripped = raw.strip()
    voice_match = _VTT_VOICE_RE.match(stripped)
    if voice_match:
        speaker = voice_match.group(1).strip()
        stripped = voice_match.group(2).strip()
    stripped = re.sub(r"<br\s*/?>", "\n", stripped, flags=re.IGNORECASE)
    stripped = _HTML_TAG_RE.sub("", stripped)
    stripped = html.unescape(stripped)
    lines = [line.strip() for line in stripped.split("\n")]
    return "\n".join(line for line in lines if line != ""), speaker


def _clean_ass_text(raw: str) -> Tuple[str, str, bool]:
    leading_match = _LEADING_ASS_TAGS_RE.match(raw)
    leading_tags = leading_match.group(0) if leading_match else ""

    has_drawing = bool(re.search(r"\\p[1-9]", raw))
    has_karaoke = bool(re.search(r"\\k[fot]?\d+", raw, re.IGNORECASE))
    text_no_tags = _ASS_OVERRIDE_RE.sub("", raw)
    plain = (
        text_no_tags.replace(r"\N", "\n")
        .replace(r"\n", "\n")
        .replace(r"\h", " ")
        .strip()
    )
    is_special = has_drawing or (has_karaoke and not plain)
    return plain, leading_tags, is_special


def parse_subtitle_content(
    content: str,
    filename: str = "subtitle.srt",
    encoding_used: str = "utf-8",
) -> SubtitleDocument:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_INPUT_FORMATS:
        raise SubtitleParseError(f"不支持的字幕后缀 .{ext}（支持 SRT、ASS、SSA、VTT）。")

    warnings: List[str] = []
    cues: List[SubtitleCue] = []
    ass_header = ""
    vtt_header = ""

    if ext == "srt":
        for idx, match in enumerate(_SRT_BLOCK_RE.finditer(content), start=1):
            start_ms = parse_timestamp_ms(match.group(2))
            end_ms = parse_timestamp_ms(match.group(3))
            if end_ms <= start_ms:
                end_ms = start_ms + 500
            clean_text, speaker = _clean_html_cue_text(match.group(4))
            if not clean_text:
                continue
            cues.append(
                SubtitleCue(
                    id=len(cues) + 1,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    source_text=clean_text,
                    speaker=speaker,
                )
            )
    elif ext == "vtt":
        blocks = re.split(r"\n{2,}", content.strip())
        if not blocks or not blocks[0].lstrip().startswith("WEBVTT"):
            raise SubtitleParseError("WebVTT 文件必须以 WEBVTT 头部开头。")
        vtt_header = blocks[0].strip()
        for block in blocks[1:]:
            b = block.strip()
            if not b or b.startswith(("NOTE", "STYLE", "REGION")):
                continue
            m = _VTT_CUE_RE.match(b)
            if not m:
                continue
            start_ms = parse_timestamp_ms(m.group(2))
            end_ms = parse_timestamp_ms(m.group(3))
            if end_ms <= start_ms:
                end_ms = start_ms + 500
            clean_text, speaker = _clean_html_cue_text(m.group(5))
            if not clean_text:
                continue
            cues.append(
                SubtitleCue(
                    id=len(cues) + 1,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    source_text=clean_text,
                    speaker=speaker,
                    raw_event_meta={"vtt_settings": (m.group(4) or "").strip()},
                )
            )
    else:
        # ASS / SSA lossless-aware parsing
        header_lines: List[str] = []
        in_events = False
        event_fields: Optional[List[str]] = None

        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.lower() == "[events]":
                in_events = True
                header_lines.append(line)
                continue
            if not in_events:
                header_lines.append(line)
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                in_events = False
                continue
            if stripped.lower().startswith("format:"):
                event_fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
                header_lines.append(line)
                continue
            event_match = re.match(r"^(Dialogue|Comment)\s*:(.*)$", line, flags=re.IGNORECASE)
            if not event_match or not event_fields:
                continue
            kind = event_match.group(1).capitalize()
            payload = event_match.group(2)
            parts = payload.split(",", len(event_fields) - 1)
            if len(parts) < len(event_fields):
                continue
            field_map = {name: val for name, val in zip(event_fields, parts)}
            start_ms = parse_timestamp_ms(field_map.get("start", "0:00:00.00"))
            end_ms = parse_timestamp_ms(field_map.get("end", "0:00:00.50"))
            raw_text = field_map.get("text", "")
            plain_text, leading_tags, special_flag = _clean_ass_text(raw_text)
            is_comment = kind.lower() == "comment"
            is_special = is_comment or special_flag

            cues.append(
                SubtitleCue(
                    id=len(cues) + 1,
                    start_ms=start_ms,
                    end_ms=max(start_ms + 10, end_ms),
                    source_text=plain_text if plain_text else raw_text,
                    speaker=field_map.get("name", "").strip(),
                    style=field_map.get("style", "Default").strip() or "Default",
                    ass_tags=leading_tags,
                    raw_event_meta={
                        "kind": kind,
                        "layer": field_map.get("layer", field_map.get("marked", "0")).strip(),
                        "marginl": field_map.get("marginl", "0").strip(),
                        "marginr": field_map.get("marginr", "0").strip(),
                        "marginv": field_map.get("marginv", "0").strip(),
                        "effect": field_map.get("effect", "").strip(),
                        "raw_text": raw_text,
                    },
                    is_special=is_special,
                    status="skipped" if is_special else "untranslated",
                )
            )
        ass_header = "\n".join(header_lines).strip()
        special_count = sum(1 for c in cues if c.is_special)
        if special_count:
            warnings.append(f"检测到 {special_count} 条 ASS 注释/特效绘图行，导出 ASS 时将原样保留，不参与翻译。")

    if not cues:
        raise SubtitleParseError("未解析到任何有效字幕行，请检查文件内容与格式。")

    detected_lang = detect_subtitle_language(cues)
    target_default = "en" if detected_lang == "zh" else "zh"

    return SubtitleDocument(
        doc_id=uuid.uuid4().hex[:12],
        filename=Path(filename).name,
        source_format=ext,  # type: ignore[arg-type]
        encoding=encoding_used,
        source_language=detected_lang,
        target_language=target_default,
        cues=cues,
        ass_header=ass_header,
        vtt_header=vtt_header,
        warnings=warnings,
    )


def split_bilingual_cues(cues: List[SubtitleCue]) -> Tuple[List[SubtitleCue], int]:
    """Split two-line bilingual source cues into source_text + target_text tracks."""
    split_count = 0
    updated: List[SubtitleCue] = []
    for cue in cues:
        if cue.is_special or cue.target_text.strip():
            updated.append(cue)
            continue
        lines = [ln.strip() for ln in cue.source_text.split("\n") if ln.strip()]
        if len(lines) >= 2:
            mid = len(lines) // 2
            first_half = "\n".join(lines[:mid])
            second_half = "\n".join(lines[mid:])
            c_copy = cue.model_copy(deep=True)
            c_copy.source_text = first_half
            c_copy.target_text = second_half
            c_copy.status = "translated"
            updated.append(c_copy)
            split_count += 1
        else:
            updated.append(cue)
    return updated, split_count


def shift_cues_timestamps(
    cues: List[SubtitleCue],
    offset_ms: int,
    cue_ids: Optional[List[int]] = None,
) -> List[SubtitleCue]:
    target_set = set(cue_ids) if cue_ids else None
    result: List[SubtitleCue] = []
    for cue in cues:
        if target_set is not None and cue.id not in target_set:
            result.append(cue)
            continue
        shifted = cue.model_copy(deep=True)
        duration = max(50, shifted.end_ms - shifted.start_ms)
        shifted.start_ms = max(0, shifted.start_ms + offset_ms)
        shifted.end_ms = max(shifted.start_ms + duration if shifted.start_ms == 0 and cue.start_ms + offset_ms < 0 else shifted.start_ms + 50, cue.end_ms + offset_ms)
        result.append(shifted)
    return result


def _compose_cue_lines(
    cue: SubtitleCue,
    track_mode: ExportTrackMode,
    fallback_to_source: bool = True,
) -> Tuple[str, Optional[str]]:
    """Return (primary_line, optional_secondary_line) according to track_mode."""
    src = cue.source_text.strip()
    tgt = cue.target_text.strip()

    if track_mode == "source_only":
        return src, None
    if track_mode == "target_only":
        return (tgt if tgt else (src if fallback_to_source else "")), None
    if track_mode == "bilingual_tgt_top":
        if tgt and tgt != src:
            return tgt, src
        return (tgt or src), None
    # bilingual_src_top
    if tgt and tgt != src:
        return src, tgt
    return src, None


DEFAULT_ASS_HEADER = """[Script Info]
; Generated by ListenTo Studio v2.0
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,54,&H00FFFFFF,&H000000FF,&H00181C24,&H80000000,0,0,0,0,100,100,0,0,1,2.6,0.8,2,40,40,42,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""


def export_document_format(
    document: SubtitleDocument,
    target_format: SubtitleFormat,
    track_mode: ExportTrackMode = "bilingual_tgt_top",
    ass_secondary_scale: int = 72,
    fallback_to_source: bool = True,
) -> str:
    """Render SubtitleDocument into SRT, VTT, ASS, SSA, or TXT."""
    if target_format not in SUPPORTED_OUTPUT_FORMATS:
        raise SubtitleParseError(f"不支持导出的字幕格式：{target_format}")

    if target_format == "txt":
        lines: List[str] = []
        for cue in document.cues:
            if cue.is_special:
                continue
            primary, secondary = _compose_cue_lines(cue, track_mode, fallback_to_source)
            if primary:
                lines.append(f"{primary}\n{secondary}" if secondary else primary)
        return "\n\n".join(lines) + "\n"

    if target_format == "srt":
        blocks: List[str] = []
        seq = 1
        for cue in document.cues:
            if cue.is_special:
                continue
            primary, secondary = _compose_cue_lines(cue, track_mode, fallback_to_source)
            if not primary:
                continue
            body = f"{primary}\n{secondary}" if secondary else primary
            blocks.append(
                f"{seq}\n{format_timestamp_srt(cue.start_ms, ',')} --> {format_timestamp_srt(cue.end_ms, ',')}\n{body}"
            )
            seq += 1
        return "\n\n".join(blocks) + "\n"

    if target_format == "vtt":
        header = document.vtt_header.strip() if document.vtt_header.strip() else "WEBVTT"
        blocks = [header]
        for cue in document.cues:
            if cue.is_special:
                continue
            primary, secondary = _compose_cue_lines(cue, track_mode, fallback_to_source)
            if not primary:
                continue
            body = f"{html.escape(primary)}\n{html.escape(secondary)}" if secondary else html.escape(primary)
            settings = cue.raw_event_meta.get("vtt_settings", "").strip()
            settings_suffix = f" {settings}" if settings else ""
            blocks.append(
                f"{format_timestamp_srt(cue.start_ms, '.')} --> {format_timestamp_srt(cue.end_ms, '.')}{settings_suffix}\n{body}"
            )
        return "\n\n".join(blocks) + "\n"

    # ASS / SSA rendering
    header = document.ass_header.strip() if (document.source_format in ("ass", "ssa") and document.ass_header.strip()) else DEFAULT_ASS_HEADER
    event_lines: List[str] = []
    sec_tag = rf"{{\fscx{ass_secondary_scale}\fscy{ass_secondary_scale}\1c&H00D0D8E2&}}"

    for cue in document.cues:
        start_ass = format_timestamp_ass(cue.start_ms)
        end_ass = format_timestamp_ass(max(cue.start_ms + 10, cue.end_ms))
        meta = cue.raw_event_meta or {}
        kind = meta.get("kind", "Dialogue")
        layer = meta.get("layer", "0")
        marginl = meta.get("marginl", "0")
        marginr = meta.get("marginr", "0")
        marginv = meta.get("marginv", "0")
        effect = meta.get("effect", "")
        style = cue.style or "Default"
        speaker = (cue.speaker or "").replace(",", " ")

        if cue.is_special and "raw_text" in meta:
            ass_text = meta["raw_text"]
        else:
            primary, secondary = _compose_cue_lines(cue, track_mode, fallback_to_source)
            if not primary:
                continue
            p_ass = primary.replace("\n", r"\N")
            if secondary:
                s_ass = secondary.replace("\n", r"\N")
                ass_text = f"{cue.ass_tags}{p_ass}\\N{sec_tag}{s_ass}"
            else:
                ass_text = f"{cue.ass_tags}{p_ass}"

        event_lines.append(
            f"{kind}: {layer},{start_ass},{end_ass},{style},{speaker},{marginl},{marginr},{marginv},{effect},{ass_text}"
        )

    ass_content = header + "\n" + "\n".join(event_lines) + "\n"
    if target_format == "ssa":
        try:
            ssa_doc = pysubs2.SSAFile.from_string(ass_content, format_="ass")
            return ssa_doc.to_string("ssa")
        except Exception:
            return ass_content
    return ass_content
