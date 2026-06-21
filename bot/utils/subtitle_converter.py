"""Subtitle conversion utilities"""
import io
import re
from aiogram.types import BufferedInputFile

# Lines with these Name field values are visual signs/overlays, not dialogue
_SIGN_NAMES = {'надпись', 'надпись'}


def _convert_time(ass_time: str) -> str:
    """Convert ASS timestamp H:MM:SS.cc to SRT HH:MM:SS,mmm"""
    ass_time = ass_time.strip()
    h, m, s_cc = ass_time.split(':', 2)
    s, cc = (s_cc.split('.') + ['00'])[:2]
    # cc is centiseconds (2 digits) → pad to milliseconds (3 digits)
    return f"{h.zfill(2)}:{m}:{s},{cc.ljust(3, '0')}"


def _parse_text(element) -> str:
    text = ','.join(element['text']).strip().replace("\\N", " ").replace("  ", " ")

    if text.startswith('—'):
        text = text.replace("—", "").strip()

    if element['fx']:
        text = f"[{text}]"
    else:
        tags = ("\\fn", "\\shad", "\\bord", "\\fade", "\\move", "\\pos", "\\fsc")
        for tag in tags:
            if tag in text:
                text = f"[{text}]"
                break

    if element['name'] != '' and element['name'] not in ['НАДПИСЬ', 'Надпись']:
        text = f"[{element['name']}] {text}"

    return re.sub(r'{.*?}', '', text).strip()


def _sort_by_time(parsed_lines):
    return parsed_lines['start']


def convert_ass_to_srt(
    file_in_bytes: bytes,
    file_name: str,
    generated_at: str = None,
    bot_version: str = None,
) -> BufferedInputFile:
    """Convert ASS subtitle file to SRT format."""
    try:
        raw_lines = file_in_bytes.decode('utf-8').split('\n')
    except UnicodeDecodeError:
        try:
            raw_lines = file_in_bytes.decode('utf-8-sig').split('\n')
        except UnicodeDecodeError:
            raw_lines = file_in_bytes.decode('latin-1').split('\n')

    lines = [line for line in raw_lines if line.startswith('Dialogue: ')]

    parsed_lines = []
    for line in lines:
        l = line.split(',', 9)
        if len(l) < 10:
            continue

        name = l[4].strip() if len(l) > 4 else ''

        # Skip visual sign/overlay lines — they are on-screen graphics, not spoken dialogue
        if name.lower() == 'надпись':
            continue

        text_raw = l[9] if len(l) > 9 else ''

        # Skip vector drawing commands (\p1 tag activates drawing mode)
        if re.search(r'\{[^}]*\\p\d', text_raw):
            continue

        parsed_lines.append({
            'start': _convert_time(l[1]),
            'end': _convert_time(l[2]),
            'name': name,
            'fx': l[8].strip() if len(l) > 8 else '',
            'text': [text_raw]
        })

    if not parsed_lines:
        raise ValueError("No dialogue lines found in ASS file")

    parsed_lines.sort(key=_sort_by_time)

    _del = []
    for i, line in enumerate(parsed_lines):
        text = _parse_text(line)
        if not text:
            _del.append(i)
            continue
        parsed_lines[i]['text'] = text
        if i > 0 and parsed_lines[i] == parsed_lines[i - 1]:
            _del.append(i)

    parsed_lines = [item for j, item in enumerate(parsed_lines) if j not in _del]

    srt_events = []
    i = 0
    while i < len(parsed_lines):
        element = parsed_lines[i]
        start = element['start']
        end = element['end']
        text = element['text']

        while i < len(parsed_lines) - 1:
            next_element = parsed_lines[i + 1]
            next_start = next_element['start']
            next_end = next_element['end']
            next_text = next_element['text']

            if end > next_start and text != next_text:
                # [sign] or [Speaker] text both start with '[' → no dash needed
                if '[' not in next_text:
                    text += '\n—' + next_text
                else:
                    text += '\n' + next_text
                end = next_end
                i += 1
            else:
                break

        srt_events.append((start, end, text))
        i += 1

    entries = []

    if generated_at:
        header = "Конвертировано ботом reales_bot"
        if bot_version:
            header += f" v{bot_version}"
        header += f" | {generated_at}"
        entries.append(f"0\n00:00:00,000 --> 00:00:00,001\n{header}\n")

    for idx, (start, end, text) in enumerate(srt_events, 1):
        entries.append(f"{idx}\n{start} --> {end}\n{text}\n")

    srt_text = "\n".join(entries)

    f_bytes = io.BytesIO()
    f_bytes.write(srt_text.encode('utf-8'))
    filename = file_name.replace('.ass', '.srt').replace('.ASS', '.srt')
    f_bytes.seek(0)
    return BufferedInputFile(f_bytes.read(), filename)
