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
from common import is_running_locally
import json
import pandas as pd

# if the code is running locally then only the dotenv module will be imported
if is_running_locally():
    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())

    aws_access_key = os.environ.get("aws-access-key-id")
    aws_secret_key = os.environ.get("aws-secret-access-key")
    region = os.environ.get("aws-region")

    s3 = boto3.client(
        "s3", aws_access_key_id=aws_access_key, aws_secret_access_key=aws_secret_key
    )
else:
    s3 = boto3.client("s3")

bucket_name = "learners-progress-report-credentials"
file_key = "google_credentials_file/generated-report-77c0d34eb762.json"

# Fetch the contents of the file from S3
try:
    response = s3.get_object(Bucket=bucket_name, Key=file_key)
    service_account_info = json.loads(response["Body"].read().decode("utf-8"))

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
    """Finds folders with a specific name on Google Drive.

    Args:
        folder_name (str): The name of the folder to search for on Google Drive.
        parent_folder_id (str|None, optional): The ID of the parent folder to search within.
            If None, the search will be performed in the root directory. Defaults to None.

    Returns:
        folders: A list of folders matching the specified criteria.
    """
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


def create_folder(folder_name, parent_folder_id=None):
    """Creates a folder with the specified name on Google Drive.

    Args:
        folder_name (str): The name of the folder to be created on Google Drive.
        parent_folder_id (str|None, optional): The ID of the parent folder in which the new folder
            will be created. If None, the folder will be created in the root directory. Defaults to None.

    Returns:
        folder_id: The folder ID of the newly created folder.
    """

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
    """Creates conditional formatting rules in a specific sheet of a Google spreadsheet.

    Conditional formatting rules vary based on the sheet type:

    - Masterclass - Progress sheet:
        - Yellow color for values less than 100.

    - Assessment - Progress sheet:
        - Red color for values less than 70.

    Args:
        google_sheet (str): The name of the Google spreadsheet.
        sheet_name (str): The name of the specific sheet within the spreadsheet.
    """

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
    """Applies bold formatting to a specified cell range in a sheet.

    Args:
        sheet (str): The name of the sheet in which text should be formatted as bold.
        cell_range (str): The cell range to which bold formatting should be applied.
        enable_bold (bool, optional): If True, the text in the specified cell range will be bold.
            If False, the bold formatting will be removed. Defaults to True.
    """
    format = {"textFormat": {"bold": enable_bold}}

    sheet.format(cell_range, format)


def generate_progress_report_from_view(view_df, sheet_name, folder_id, file_name):
    """
    Prerequisites:
    Ensure that the provided Google Sheet has been shared with the email address: `generated-report@generated-report.iam.gserviceaccount.com`.

    To Share the Google Sheet:
    1. Open the Google Sheet.
    2. Click on "File" -> "Share."
    3. Under "Share with others," add the email address and grant it editor access.

    Args:
        view_df (DataFrame): The DataFrame containing learner data for a specific client.
        sheet_name (str): The name of the sheet where the data will be stored.
        folder_id (str): The ID of the folder in which the Google Sheet should be placed.
        file_name (str): The desired name for the Google Sheet file.

    Process:
    Stores the data from the view_df DataFrame into a specified Google Sheet.
    """
    # console.cloud.google.com:- ```generated-report@generated-report.iam.gserviceaccount.com```
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        service_account_info, scopes=scopes
    )

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

    # check whether the sheet exists in the google spreadsheet or not
    try:
        # If yes, then open the given sheet
        sheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound as err:
        # Else add the sheet and then open it
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

    # First remove the boldness from the cell range
    # will remove the bold from the cell as below logic inserts 2 cell, and thus if the bold formatting
    # is not removed then the bold formatting of A1:B1 will move to A3:B3, and like these it will shift
    # in each iteration. It is necessary to remove the boldness before inserting the rows.
    bold_text(sheet=sheet, cell_range="A1:B1", enable_bold=False)

    # Also, remove the bold formatting from the 3 row
    bold_text(sheet=sheet, cell_range="3:3", enable_bold=False)

    # # Now sort the columns of Assessment_progress based on the scores in descending order
    # if sheet_name=="Assessment - Progress":
    # sort_data(sheet=sheet)

    # Insert an empty row at the top (row 1)
    sheet.insert_row(values=None, index=1)
    sheet.insert_row(values=None, index=1)

    # Update cell A1 with "Refresh" and cell B1 with the current timestamp
    sheet.update("A1", "Refresh_at: ")

    # datetime.now() is giving different times on local and on lambda function, so manually calculate IST
    # by fetching the utc timestamp and adding 5:30 to it
    utc_date = datetime.utcnow()
    new_hour = utc_date.hour + 5
    new_minute = utc_date.minute + 30
    if new_minute > 59:
        new_minute = new_minute - 60
        new_hour += 1

    # Based on new_hour and new_minutes, find the IST approx date
    ist_date = datetime(
        utc_date.year,
        utc_date.month,
        utc_date.day,
        new_hour,
        new_minute,
        utc_date.second,
    )
    sheet.update("B1", ist_date.strftime("%Y-%m-%d %H:%M:%S"))

    # Now bold the text A1:B1 that contains Refresh_at
    bold_text(sheet=sheet, cell_range="A1:B1")

    # Now bold the 3 row that contains the headers
    bold_text(sheet=sheet, cell_range="3:3")

    # Now once the sheet get created, perform conditional formatting
    conditional_formatting(google_sheet=sh, sheet_name=sheet_name)


def update_assessment_data_with_google_sheet_data(new_assessment_data_df, file_name, folder_id, sheet_name):
    """
    Updates the 'new_assessment_data_df' DataFrame with data from a specified Google Sheet. The attribute
    presented in google sheet, but are not present in new_assessment_data_df, will be inserted in new_assessment_data_df
    so that if any attribute reflected in google sheet won't be removed from the sheet.
    
    Parameters:
    - new_assessment_data_df (pd.DataFrame): The DataFrame to be updated.
    - file_name (str): The name of the Google Sheet file.
    - folder_id (str): The folder ID where the Google Sheet is located.
    - sheet_name (str): The name of the specific sheet within the Google Sheet.

    Returns:
    - pd.DataFrame: The updated DataFrame.
    """
    # Sort the records based on the participant_email
    # new_assessment_data_df.sort_values("participant_email", inplace=True)

    # Now fetch the data from Google Sheet
    sheet_data_df = fetch_data_from_google_sheet(
        file_name=file_name,
        folder_id=folder_id,
        sheet_name=sheet_name,
    )

    # Remove the column "Average"
    if "Average" in sheet_data_df.columns:
        sheet_data_df = sheet_data_df.drop(columns=["Average"])

    # Also sort the records of sheet_data_df
    # sheet_data_df.sort_values("participant_email", inplace=True)

    # Find columns present in sheet_data_df but not in new_assessment_data_df
    missing_columns = [
        col for col in sheet_data_df.columns if col not in new_assessment_data_df.columns
    ]

    # if there are no any column that is present in the google sheet but are not there in the new_assessment_data_df
    if not missing_columns:
        print("There is no such column in google sheet that is not present in new_assessment_data_df!")
        return new_assessment_data_df

    # Insert the missing columns into new_assessment_data_df at the appropriate positions (after the last matching column)
    #for col in missing_columns:
    # print(f"{col} column is not present in new assessment sheet, but is there in the google sheet.")

    # Now append participant_email with mssing columns for merge purpose
    missing_columns.append("participant_email")

    new_assessment_data_df = new_assessment_data_df.merge(
            sheet_data_df[missing_columns],
            how="inner",
            on="participant_email"
        )
    
    return new_assessment_data_df


# Function to generate reports
def generate_report(df, sheet_name, value_column, folder_id, file_name):
    """Pivots the DataFrame, converting project names from rows to columns.
    If the sheet name is 'Assessment - Progress,' it calculates the average of assessments.
    The pivoted DataFrame is then stored in a Google Sheet using the 'generate_progress_report_from_view' function.

    Args:
        df (DataFrame): The DataFrame containing Masterclass or assessment data in a single column,
            which needs to be pivoted before reflecting in the Google Sheet.
        sheet_name (str): The name of the sheet where the pivoted DataFrame should be reflected.
        value_column (str): The name of the column whose values will be distributed across multiple columns
            during the DataFrame pivot operation.
        folder_id (str): The ID of the folder where the Google Sheet is located in Google Drive.
        file_name (str): The name of the Google Sheet where the pivoted DataFrame should be reflected.
    """

    if not df.empty:
        df.sort_values(value_column, ascending=False, inplace=True)

        df_drop_duplicates = df.drop_duplicates(
            subset=["participant_name", "participant_email", "project_name"],
            keep="first",
        )

        # pivot the columns
        view_df = df_drop_duplicates.pivot(
            index=["participant_email", "participant_name"],
            columns="project_name",
            values=value_column,
        ).reset_index()

        # In case when sheet is related to assessment, we need to fetch one more attribute
        # that is Average
        if sheet_name == "Assessment - Progress":
            # TODO: After calculation of average write the logic where the columns that are not there in
            # view_df but are present in sheet should be added to view_df (Logic is in progress at below)
            # Sort the records based on the participant_email
            view_df = update_assessment_data_with_google_sheet_data(
                new_assessment_data_df=view_df,
                file_name=file_name,
                folder_id=folder_id,
                sheet_name=sheet_name
            )

            # Before calculating the average, iterate through each assessment and replace the null values with 0
            view_df.fillna(0, inplace=True)

            other_columns = ["participant_email", "participant_name"]
            assessment_columns = [col for col in view_df.columns if col not in other_columns]

            # Convert the assessment columns into integer
            for col in assessment_columns:
                if view_df[col].dtypes != "int64":
                    view_df[col] = pd.to_numeric(view_df[col], errors='coerce')
                    view_df[col] = view_df[col].fillna(0).astype("int64")

            # calculate the average
            if len(assessment_columns) > 1:
            # Find the average
                view_df["Average"] = view_df[assessment_columns].mean(axis=1).round()
                view_df.sort_values("Average", ascending=False, inplace=True)
            else:
                view_df.sort_values(assessment_columns[0], ascending=False, inplace=True)

        generate_progress_report_from_view(
            view_df=view_df,
            sheet_name=sheet_name,
            folder_id=folder_id,
            file_name=file_name,
        )
        print(f"The {sheet_name} report was successfully generated ✅")
    else:
        print(f"There are no {sheet_name} records to update!")


def fetch_data_from_google_sheet(file_name, sheet_name, folder_id):
    """
    Fetches data from a Google Sheet located in a specific folder.

    Args:
        file_name (str): The name of the spreadsheet.
        sheet_name (str): The name of the Google Sheet.
        folder_id (str): The ID of the folder containing the Google Sheet.

    Returns:
        pd.DataFrame: A Pandas DataFrame containing the data from the Google Sheet.
    """
    # Define the Google Sheets and Drive API scopes
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        service_account_info, scopes=scopes
    )

    gc = gspread.authorize(credentials)

    try:
        # First try to open the spreadsheet, if spreadsheet is not there, it will throw the error
        spreadsheet = gc.open(title=file_name, folder_id=folder_id)
    except Exception as error:
        print(error)

    # Select a specific worksheet (e.g., the first sheet)
    worksheet = spreadsheet.worksheet(sheet_name)

    # Get all values from the worksheet
    data = worksheet.get_all_values()

    # Convert the data into a DataFrame (assuming the first row contains column headers)
    df = pd.DataFrame(data[3:], columns=data[2])

    return df