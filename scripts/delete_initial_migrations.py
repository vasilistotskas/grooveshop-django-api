import glob
import os

# Set the path to the project root directory
root_dir = "../."

# Define a list of folders to exclude
excluded_folders = ["core"]  # Add the folders you want to exclude

# Find all the Django apps in the project
app_dirs = glob.glob(os.path.join(root_dir, "*/migrations"))

# Iterate over the app directories
for app_dir in app_dirs:
    # Get the app name from the directory path
    app_name = os.path.basename(os.path.dirname(app_dir))

    # Check if the app is in the excluded list
    if app_name in excluded_folders:
        print(f"Ignoring migrations in {app_name}")
        continue

    # Find all files in 'migrations' folder and delete all except __init__.py
    migration_files = glob.glob(os.path.join(app_dir, "*"))
    print(f"len(migration_files) in {app_name}: {len(migration_files)}")
    if len(migration_files) == 0:
        print(f"No migration files found in {app_name}")
        pass

    for migration_file in migration_files:
        if migration_file.endswith("__init__.py") or migration_file.endswith("__pycache__"):
            continue
        os.remove(migration_file)
        print(f"Deleted: {migration_file} in {app_name}")
