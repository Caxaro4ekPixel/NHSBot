"""Subtitle conversion utilities"""
import io
import re
from aiogram.types import BufferedInputFile


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
        text = f"({element['name']}) {text}"

    return re.sub(r'{.*?}', '', text)


def _sort_by_time(parsed_lines):
    return parsed_lines['start']


def convert_ass_to_srt(file_in_bytes: bytes, file_name: str) -> BufferedInputFile:
    """Convert ASS subtitle file to SRT format"""
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
        parsed_lines.append({
            'start': l[1].replace(".", ",").strip(), 
            'end': l[2].replace(".", ",").strip(),
            'name': l[3].strip() if len(l) > 3 else '',
            'fx': l[8].strip() if len(l) > 8 else '',
            'text': [l[9]] if len(l) > 9 else []
        })
    
    if not parsed_lines:
        raise ValueError("No dialogue lines found in ASS file")
    
    parsed_lines.sort(key=_sort_by_time)

    _del = []
    for i, line in enumerate(parsed_lines):
        text = parsed_lines[i]['text'] = _parse_text(line)
        start = line['start']
        end = line['end']

        if i > 0:
            if parsed_lines[i] == parsed_lines[i-1]:
                _del.append(i)

    parsed_lines = [i for j, i in enumerate(parsed_lines) if j not in _del]

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

    srt_text = "\n".join([
            f"{i}\n{start} --> {end}\n{text}\n" 
            for i, (start, end, text) 
            in enumerate(srt_events, 1)
        ])
    
    f_bytes = io.BytesIO()
    f_bytes.write(bytes(srt_text, 'utf-8'))
    filename = file_name.replace('.ass', '.srt').replace('.ASS', '.srt')
    f_bytes.seek(0)
    file = BufferedInputFile(f_bytes.read(), filename)
    return file

