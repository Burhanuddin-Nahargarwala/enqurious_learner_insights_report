import os

def is_running_locally():
    # Check if the 'LAMBDA_TASK_ROOT' environment variable is set.
    # Lambda sets this variable, so if it's not set, the code is likely running locally.
    return 'LAMBDA_TASK_ROOT' not in os.environ