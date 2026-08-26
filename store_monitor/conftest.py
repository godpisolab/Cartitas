"""Vive en la RAÍZ de store_monitor/ (no dentro de tests/) a propósito:
pytest añade el directorio de cada conftest.py a sys.path, así que esto es
lo que permite `import base_script` (y `from scrapers.xxx import ...`)
desde cualquier test sin depender del directorio desde el que se invoque
`pytest`."""
