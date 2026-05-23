import re

SECTION_PATTERNS = {
    "abstract": [r"^abstract"],
    "introduction": [r"^introduction", r"^background"],
    "methods": [r"^methods?", r"^materials and methods", r"^experimental"],
    "results": [r"^results?", r"^findings"],
    "discussion": [r"^discussion", r"^conclusion", r"^conclusions"],
    "supplementary": [r"^supplementary", r"^supplemental"]
}

class SectionSplitter:

    def split(self, text: str) -> list:
        sections = self._split_by_newlines(text)
        if len(sections) > 1:
            return sections
        inline = self._split_inline(text)
        if len(inline) > len(sections):
            return inline
        return sections

    def _split_by_newlines(self, text: str) -> list:
        lines = text.split("\n")
        sections = []
        current_section = None
        current_lines = []

        for line in lines:
            detected = self._detect_section(line.strip())
            if detected:
                if current_lines and current_section:
                    sections.append({
                        "section": current_section,
                        "text": " ".join(current_lines).strip()
                    })
                current_section = detected
                remainder = self._remove_section_label(line.strip(), detected)
                current_lines = [remainder] if remainder else []
            else:
                if line.strip():
                    current_lines.append(line.strip())

        if current_lines and current_section:
            sections.append({
                "section": current_section,
                "text": " ".join(current_lines).strip()
            })

        return sections

    def _split_inline(self, text: str) -> list:
        sections = []
        found_positions = []

        for section_name, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                inline_pattern = pattern.lstrip("^")
                for match in re.finditer(inline_pattern, text, re.IGNORECASE):
                    start = match.start()
                    if start == 0 or text[start-1] == "\n" or text[max(0, start-2):start] in [". ", ".\n"]:
                        found_positions.append((start, match.end(), section_name))

        if not found_positions:
            return [{"section": "abstract", "text": text.strip()}]

        found_positions.sort(key=lambda x: x[0])

        # remove duplicates keeping first occurrence of each section
        seen = {}
        unique_positions = []
        for pos in found_positions:
            if pos[2] not in seen:
                seen[pos[2]] = True
                unique_positions.append(pos)

        unique_positions.sort(key=lambda x: x[0])

        for i, (start, end, section_name) in enumerate(unique_positions):
            next_start = unique_positions[i + 1][0] if i + 1 < len(unique_positions) else len(text)
            section_text = text[end:next_start].strip()
            if section_text:
                sections.append({
                    "section": section_name,
                    "text": section_text
                })

        return sections

    def _detect_section(self, line: str) -> str:
        line_lower = line.lower()
        for section, patterns in SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, line_lower):
                    return section
        return None

    def _remove_section_label(self, line: str, section: str) -> str:
        line_lower = line.lower()
        for pattern in SECTION_PATTERNS[section]:
            match = re.match(pattern, line_lower)
            if match:
                return line[match.end():].strip()
        return line