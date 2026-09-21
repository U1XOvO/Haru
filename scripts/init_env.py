"""Create initial configuration without replacing an existing .env."""
from pathlib import Path


def initialize(root):
    try:
        with (root / '.env').open('x', encoding='utf-8') as output:
            output.write((root / '.env.example').read_text(encoding='utf-8'))
    except FileExistsError:
        pass


if __name__ == '__main__':
    initialize(Path(__file__).resolve().parents[1])
