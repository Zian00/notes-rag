import re

def extract_lecture_id(text: str):
    """
    Extract lecture/tutorial/lab number and normalize it.
    Returns e.g. lecture_3, tutorial_2, lab_1
    """
    match = re.search(r'(lecture|lec|lect|tutorial|tut|lab)\s*(\d+)', text.lower())
    if not match:
        return None

    type_map = {
        "lec": "lecture",
        "lect": "lecture",
        "lecture": "lecture",
        "tut": "tutorial",
        "tutorial": "tutorial",
        "lab": "lab"
    }

    return f"{type_map[match.group(1)]}_{match.group(2)}"
