import os
import sys
import types
from unittest.mock import MagicMock


ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
for path in (ROOT, SRC):
    if path not in sys.path:
        sys.path.insert(0, path)


def _stub_module(name: str, **attrs) -> types.ModuleType:
    """Return a MagicMock-based stub module and register it in sys.modules."""
    mod = types.ModuleType(name)
    for attr, value in attrs.items():
        setattr(mod, attr, value)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


# tiktoken
if "tiktoken" not in sys.modules:
    _encoder_stub = MagicMock()
    _encoder_stub.encode.side_effect = lambda text: text.split()  # token ≈ word
    _tiktoken = _stub_module("tiktoken")
    _tiktoken.get_encoding = MagicMock(return_value=_encoder_stub)

# torch
if "torch" not in sys.modules:
    _torch = _stub_module("torch")
    _torch.no_grad = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=None),
                                                       __exit__=MagicMock(return_value=False)))
    _stub_module("torch.nn")
    _stub_module("torch.nn.functional")

# transformers
if "transformers" not in sys.modules:
    _transformers = _stub_module("transformers")
    _transformers.AutoTokenizer = MagicMock()
    _transformers.AutoModelForSequenceClassification = MagicMock()
    _stub_module("transformers.modeling_outputs")

# python-dotenv
if "dotenv" not in sys.modules:
    _dotenv = _stub_module("dotenv")
    _dotenv.load_dotenv = MagicMock()

# fitz (PyMuPDF)
if "fitz" not in sys.modules:
    _fitz = _stub_module("fitz")

    class _FakePage:
        def get_text(self):
            return "fake pdf page text"

    class _FakeDoc:
        def __iter__(self):
            return iter([_FakePage()])

    _fitz.open = MagicMock(return_value=_FakeDoc())

# bs4 (beautifulsoup4)
if "bs4" not in sys.modules:
    _bs4 = _stub_module("bs4")

    class _FakeTag:
        def __init__(self, text=""):
            self._text = text

        def decompose(self):
            pass

        def get_text(self, separator=" ", strip=False):
            return self._text

        def find(self, tag):
            return None

    class _FakeSoup:
        def __init__(self, content, parser):
            self._content = content

        def __call__(self, tags):
            return []

        def find(self, tag):
            return None

        def get_text(self, separator=" ", strip=False):
            # strip HTML tags naively for tests
            import re
            return re.sub(r"<[^>]+>", " ", self._content).strip()

    _bs4.BeautifulSoup = _FakeSoup

# openai and instructor
if "openai" not in sys.modules:
    _openai = _stub_module("openai")
    _openai.OpenAI = MagicMock()

if "instructor" not in sys.modules:
    _instructor = _stub_module("instructor")
    _instructor.patch = MagicMock(return_value=MagicMock())
