import os

def is_running_locally():
    # Check if the 'LAMBDA_TASK_ROOT' environment variable is set.
    # Lambda sets this variable, so if it's not set, the code is likely running locally.
    return 'LAMBDA_TASK_ROOT' not in os.environ

# if the code is running locally then only the dotenv module will be imported
if is_running_locally():
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())


# database credentials
HOST = os.environ.get("HOST")
DBNAME = os.environ.get("DBNAME")
USER = os.environ.get("READONLY_USER")
PASSWORD = os.environ.get("READONLY_PASSWORD")


PROGRESS_REPORT_FOLDER_NAME = os.environ.get('FOLDER_NAME')