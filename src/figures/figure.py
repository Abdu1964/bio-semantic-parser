"""One figure taken from a paper: image, label and caption."""
from dataclasses import dataclass, field


@dataclass
class Figure:
    fig_id: str                                  # stable within the paper, e.g. "p3_img1" or "F2"
    label: str = ""                              # "Figure 2"
    caption: str = ""
    page: int = 0                                # 1-based PDF page; 0 when unknown
    source: str = ""                             # "pdf" or "jats"
    image: bytes = field(default=b"", repr=False)
    ext: str = "png"
    image_file: str = ""                         # file name inside the paper, when known
    region: str = "image"                        # "image", or "page" for a full-page render

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "image"}
