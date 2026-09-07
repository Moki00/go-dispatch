"""
Reads the directory tree of the project, prints it to the console,
and exports the clean layout to structure.txt.
"""

from pathlib import Path

# Directories and files to ignore
IGNORE_DIRS = {".git", "venv", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
IGNORE_FILES = {".DS_Store"}


def build_tree(dir_path: Path, prefix: str = "") -> list[str]:
    lines = []
    
    # Collect items, filtering out ignored names
    items = [
        item for item in sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if item.name not in IGNORE_DIRS and item.name not in IGNORE_FILES
    ]

    total = len(items)
    for index, item in enumerate(items):
        is_last = (index == total - 1)
        connector = "└── " if is_last else "├── "
        
        if item.is_dir():
            lines.append(f"{prefix}{connector}{item.name}/")
            extension = "    " if is_last else "│   "
            lines.extend(build_tree(item, prefix=prefix + extension))
        else:
            lines.append(f"{prefix}{connector}{item.name}")

    return lines


def main():
    root_dir = Path(__file__).resolve().parent
    project_name = root_dir.name

    tree_lines = [f"{project_name}/"] + build_tree(root_dir)
    output_text = "\n".join(tree_lines)

    # Print to console
    print(output_text)

    # Write to structure.txt
    output_path = root_dir / "structure.txt"
    output_path.write_text(output_text, encoding="utf-8")
    print(f"\nSaved layout to: {output_path}")


if __name__ == "__main__":
    main()