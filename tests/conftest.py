import os


# Production defaults to PostgreSQL; unit tests opt into the legacy-compatible
# in-process SQLite adapter explicitly before application modules are imported.
os.environ["DB_TYPE"] = "sqlite"
