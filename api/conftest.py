"""Vive en la RAÍZ de api/ (no dentro de tests/) a propósito: pytest añade
el directorio de cada conftest.py a sys.path, así que esto es lo que
permite `import main`, `import db`, `from models... import ...` etc. desde
cualquier test sin depender del directorio desde el que se invoque
`pytest` -- mismo patrón que store_monitor/conftest.py."""
