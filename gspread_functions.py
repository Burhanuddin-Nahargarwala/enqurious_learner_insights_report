from gspread_dataframe import set_with_dataframe
from google.oauth2.service_account import Credentials
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from datetime import datetime
from gspread_formatting import *

import os
import gspread
import boto3
from googleapiclient.discovery import build
from google.oauth2 import service_account
from common import is_running_locally
import json


# if the code is running locally then only the dotenv module will be imported
if is_running_locally():
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    aws_access_key = os.environ.get('aws-access-key-id')
    aws_secret_key = os.environ.get('aws-secret-access-key')
    region = os.environ.get('aws-region')
    bucket_name = "learners-progress-report-credentials"
    file_key = "google_credentials_file/generated-report-77c0d34eb762.json"

    s3 = boto3.client(
        's3',
        aws_access_key_id=aws_access_key, 
        aws_secret_access_key=aws_secret_key
    )
else:   
    s3=boto3.client('s3')


# Fetch the contents of the file from S3
try:
    response = s3.get_object(Bucket=bucket_name, Key=file_key)
    service_account_info = json.loads(response['Body'].read().decode('utf-8'))
    
    # # Print or process the file content as needed
    # print(file_content.decode('utf-8'))  # Assuming it's a text file
except Exception as e:
    print(f"An error occurred: {str(e)}")
    exit()


# # Set the path to your credentials JSON file
# # TODO: Move the file to s3 and assign that path here
# credentials_file = os.environ.get("GSHEET_CREDENTIAL_FILE_PATH")

# # Initialize the Google Drive API with your credentials
credentials = Credentials.from_service_account_info(
    service_account_info, scopes=["https://www.googleapis.com/auth/drive"]
)

drive_service = build("drive", "v3", credentials=credentials)


def search_folder(folder_name, parent_folder_id=None):
    if parent_folder_id:
        # Search for the folder by name
        query = f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and name='{folder_name}'"
    else:
        # Search for the folder by name
        query = (
            f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
        )

    try:
        results = drive_service.files().list(q=query).execute()
    except Exception as e:
        print(f"Error: {e}")
        return None

    # Check if any matching folders were found
    folders = results.get("files", [])

    return folders


def create_folder(folder_name, parent_folder_id="1AzkjLoxMHu6oA7CQHSNeNBQTUKadF3_D"):
    # Create the folder metadata
    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }

    if parent_folder_id:
        folder_metadata["parents"] = [parent_folder_id]

    # Create the folder on Google Drive
    created_folder = (
        drive_service.files().create(body=folder_metadata, fields="id").execute()
    )

    # Get the ID of the newly created folder
    folder_id = created_folder.get("id")

    # Print the folder ID
    print(f'Folder "{folder_name}" created with ID: {folder_id}')

    return folder_id


def conditional_formatting(google_sheet, sheet_name):
    # fetch the sheet
    sheet = google_sheet.worksheet(sheet_name)

    # Total_rows
    rows = len(sheet.get_all_values())

    # Total_columns
    columns = len(sheet.get_all_values()[0])

    # fetch the last_Col_name
    last_col_count = columns  # sh.col_count
    last_col_name = chr(65 + (last_col_count - 1) % 26)
    if last_col_count > 26:
        last_col_name = chr(64 + (last_col_count - 1) // 26) + last_col_name

    # fetch the index of sheet
    range = f"A1:{last_col_name}{rows}"

    if sheet_name == "Masterclass - Progress":
        # Define the formatting rules
        rule = ConditionalFormatRule(
            ranges=[GridRange.from_a1_range(range, sheet)],
            booleanRule=BooleanRule(
                condition=BooleanCondition("NUMBER_LESS", ["100"]),
                format=CellFormat(backgroundColor=color(1, 1, 0.6)),
            ),
        )

    elif sheet_name == "Assessment - Progress":
        rule = ConditionalFormatRule(
            ranges=[GridRange.from_a1_range(range, sheet)],
            booleanRule=BooleanRule(
                condition=BooleanCondition("NUMBER_LESS", ["70"]),
                format=CellFormat(backgroundColor=color(1, 0.8, 0.8)),
            ),
        )

    # once rule is assigned, format the sheet with the given rule
    rules = get_conditional_format_rules(sheet)
    rules.append(rule)
    rules.save()


def bold_text(sheet, cell_range, enable_bold: bool = True):
    format = {"textFormat": {"bold": enable_bold}}

    sheet.format(cell_range, format)


def generate_progress_report_from_view(view_df, sheet_name, folder_id, file_name):
    """
    prerequisites: If the function doesn't store the given df into the Google sheet then check that you
    have given the editor access of your Google sheet to the given email id:- `generated-report@generated-report.iam.gserviceaccount.com`
    Steps:-
    1. Go to Google Sheet -> File -> Share
    2. Click on Share with Others
    3. Add the given email_id and give the editor access to it.

    :param client_name: name of the client whose learner's data is there in the view_df dataframe
    :param view_df:  containing the learner's data of a particular client

    process: Store the view_df dataframe into the Google sheet.
    """
    # console.cloud.google.com:- ```generated-report@generated-report.iam.gserviceaccount.com```
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(service_account_info, scopes=scopes)

    gc = gspread.authorize(credentials)

    try:
        # First try to open the spreadsheet, if spreadsheet is not there, it will throw the error
        sh = gc.open(title=file_name, folder_id=folder_id)
    except gspread.exceptions.SpreadsheetNotFound as err:
        # When it throws the error, create the given spreadsheet
        sh = gc.create(
            title=file_name, folder_id=folder_id
        )  # . -> to create a spreadsheet

    # worksheet = sh.add_worksheet(title=program_code, rows='100', cols='9') # -> add a sheet to existing worksheet

    gauth = GoogleAuth()
    drive = GoogleDrive(gauth)

    # open a google sheet
    # gs = gc.open_by_url(GSHEET_URL)  # select a work sheet from its name

    # Now add a gsheet
    # Now fetch the current time
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # check whether the sheet exists in the google sheet or not
    try:
        # Now open the given sheet
        sheet = sh.worksheet(sheet_name)

        set_with_dataframe(
            worksheet=sheet,
            dataframe=view_df,
            include_index=False,
            include_column_header=True,
            resize=True,
        )

        # First remove the boldness from the cell range
        # will remove the bold from the cell as below logic inserts 2 cell, and thus if the bold formatting
        # is not removed then the bold formatting of A1:B1 will move to A3:B3, and like these it will shift
        # in each iteration. It is necessary to remove the boldness before inserting the rows.
        bold_text(sheet=sheet, cell_range="A1:B1", enable_bold=False)

        # Also, remove the bol formatting from the 3 row
        bold_text(sheet=sheet, cell_range="3:3", enable_bold=False)

        # Insert an empty row at the top (row 1)
        sheet.insert_row(values=None, index=1)
        sheet.insert_row(values=None, index=1)

        # Update cell A1 with "Refresh" and cell B1 with the current timestamp
        sheet.update("A1", "Refresh_at: ")
        sheet.update("B1", current_timestamp)
    except gspread.exceptions.WorksheetNotFound as err:
        sheet = sh.add_worksheet(
            title=sheet_name, rows=len(view_df), cols=len(view_df.columns)
        )

        set_with_dataframe(
            worksheet=sheet,
            dataframe=view_df,
            include_index=False,
            include_column_header=True,
            resize=True,
        )

        # Insert an empty row at the top (row 1)
        sheet.insert_row(values=None, index=1)
        sheet.insert_row(values=None, index=1)

        # Update cell A1 with "Refresh" and cell B1 with the current timestamp
        sheet.update("A1", "Refresh_at: ")
        sheet.update("B1", current_timestamp)

    # Now bold the text A1:B1 that contains Refresh_at
    bold_text(sheet=sheet, cell_range="A1:B1")

    # Now bold the 3 row that contains the headers
    bold_text(sheet=sheet, cell_range="3:3")

    # Now once the sheet get created, perform conditional formatting
    conditional_formatting(google_sheet=sh, sheet_name=sheet_name)


# Function to generate reports
def generate_report(df, sheet_name, value_column, folder_id, file_name):
    if not df.empty:
        df.sort_values(value_column, ascending=False, inplace=True)
        df_drop_duplicates = df.drop_duplicates(
            subset=["participant_name", "participant_email", "project_name"],
            keep="first",
        )
        view_df = df_drop_duplicates.pivot(
            index=["participant_email", "participant_name"],
            columns="project_name",
            values=value_column,
        ).reset_index()
        generate_progress_report_from_view(
            view_df=view_df,
            sheet_name=sheet_name,
            folder_id=folder_id,
            file_name=file_name,
        )
        print(f"The {sheet_name} report was successfully generated ✅")
    else:
        print(f"There are no {sheet_name} records to update!")
