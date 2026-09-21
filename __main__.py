from .app import create_app
from .ui.textual_backend import TextualBackend


if __name__ == "__main__":
    create_app(TextualBackend()).run()