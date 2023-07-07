import glob
import os

# Set the path to the project root directory
root_dir = "../."

# Find all the Django apps in the project
app_dirs = glob.glob(os.path.join(root_dir, "*/migrations"))

# Iterate over the app directories
for app_dir in app_dirs:
    # Find all files in 'migrations' folder and delete all except __init__.py
    migration_files = glob.glob(os.path.join(app_dir, "*"))
    print("len(migration_files):", len(migration_files))
    if len(migration_files) == 0:
        print("No migration files found in {}".format(app_dir))
        pass

    for migration_file in migration_files:
        if migration_file.endswith("__init__.py") or migration_file.endswith(
            "__pycache__"
        ):
            continue
        os.remove(migration_file)
        print("Deleted: ", migration_file)
