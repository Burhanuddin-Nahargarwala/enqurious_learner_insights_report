# Import the required libraries
import json
import pandas as pd

from gspread_functions import search_folder, create_folder, generate_report
from db import conn, cursor, close_cursor_and_conn
from dateutil.relativedelta import relativedelta
from datetime import datetime
from common import PROGRESS_REPORT_FOLDER_NAME


def filter_within_proxy_period(df, months_to_keep):
    """Filters records in the dataframe to retain only those within the specified proxy period.

    Args:
        dataframe (DataFrame): The input DataFrame containing records to filter.
        months_to_keep (int): The number of months that defines the proxy period.

    Returns:
        DataFrame: A new DataFrame containing only records within the proxy period.
    """

    df["within_proxy_period"] = df["created_at"].apply(
        lambda date_str: date_str.date()
        > (datetime.now().date() - relativedelta(months=months_to_keep))
    )

    df = df[df["within_proxy_period"] == True].reset_index(drop=True)

    # Afer filter drop the column
    df.drop("within_proxy_period", axis=1, inplace=True)

    return df


def skills_fact_calculation(cursor, client_id):
    """Calculates the total scores of a specific project and the individual scores of participants in that project.

    Args:
        cursor (psycopg2.extensions.cursor): The psycopg2 database cursor for executing SQL queries.
        client_id (str): The client ID used to filter records related to a specific client and perform calculations.

    Returns:
        DataFrame: A new DataFrame containing individual learner scores and the total project score, presented as percentages.
    """

    # now fetch the skills_fact table
    cursor.execute(
        f"SELECT * FROM skills_fact WHERE client_id='{client_id}' AND is_current=True;"
    )

    # Now fetch the result
    skills_fact_result = cursor.fetchall()
    skills_fact_columns = [desc[0] for desc in cursor.description]

    skills_fact_df = pd.DataFrame(skills_fact_result, columns=skills_fact_columns)

    # Filter the table based on proxy period
    skills_fact_df = filter_within_proxy_period(skills_fact_df, months_to_keep=3)
    # print("Skills fact created_at: ", skills_fact_df["created_at"].unique())

    # Take the skills_fact at the order level granularity
    skills_fact_df_calculation = (
        skills_fact_df.groupby(
            ["participant_name", "participant_email", "order_id", "project_name"]
        )
        .agg(
            {
                "score": "sum",
                "total": "sum",
            }
        )
        .reset_index()
        .rename({"score": "Scores", "total": "Total"}, axis=1)
    )

    skills_fact_df_calculation["Scores (%)"] = round(
        (skills_fact_df_calculation["Scores"] / skills_fact_df_calculation["Total"])
        * 100,
        0,
    )

    skills_fact_df_calculation["Scores (%)"] = skills_fact_df_calculation[
        "Scores (%)"
    ].astype(int)

    return skills_fact_df_calculation


def progress_fact_calculation(cursor, client_id):
    """Calculates the progress percentage of participants in their projects. Progress is based on the number of
    project inputs attempted compared to the total number of inputs.

    Args:
        cursor (psycopg2.extensions.cursor): The psycopg2 database cursor for executing SQL queries.
        client_id (str): The client ID used to filter records related to a specific client and perform calculations.

    Returns:
        DataFrame: A new DataFrame containing the progress percentages of individual learners for their respective projects.
    """

    # now fetch the progress fact table
    cursor.execute(
        f"SELECT * FROM progress_fact WHERE client_id='{client_id}' AND is_current=True;"
    )

    # Now fetch the result
    progress_fact_result = cursor.fetchall()
    progress_fact_columns = [desc[0] for desc in cursor.description]

    progress_fact_df = pd.DataFrame(progress_fact_result, columns=progress_fact_columns)

    # Filter the table based on proxy period
    progress_fact_df = filter_within_proxy_period(progress_fact_df, months_to_keep=3)
    # print("Progress fact created_at: ", progress_fact_df["created_at"].unique())

    # Now fetch the percentage of progress
    progress_fact_df_calculation = (
        progress_fact_df.groupby(
            [
                "participants_name",
                "participants_email",
                "order_id",
                "project_name",
                "learner_status_by_order",
            ],
        )
        .agg({"completed_inputs_by_activity": "sum", "total_inputs_by_activity": "sum"})
        .reset_index()
    )

    progress_fact_df_calculation["completed_inputs_by_activity"] = pd.to_numeric(
        progress_fact_df_calculation["completed_inputs_by_activity"], errors="coerce"
    ).fillna(0)

    progress_fact_df_calculation["total_inputs_by_activity"] = pd.to_numeric(
        progress_fact_df_calculation["total_inputs_by_activity"], errors="coerce"
    ).fillna(0)

    # find the progress of learner, if learner has completed 5 inputs out of 10, thus the progress would be 5/10 = 50%
    progress_fact_df_calculation["Progress (%)"] = round(
        (
            progress_fact_df_calculation["completed_inputs_by_activity"]
            / progress_fact_df_calculation["total_inputs_by_activity"]
        )
        * 100,
        2,
    )

    # Convert the Scores into int, as we want the integer value of scores in final report
    progress_fact_df_calculation["Progress (%)"] = (
        progress_fact_df_calculation["Progress (%)"].fillna(0).astype(int)
    )

    return progress_fact_df_calculation


def get_folder_id(folder_name, parent_id=None):
    """Fetches the folder ID of a specified folder in Google Drive. If the folder doesn't exist,
    it creates the folder and returns its ID.

    Args:
        folder_name (str): The name of the folder to search for on Google Drive.
        parent_id (str|None, optional): The ID of the parent folder to search within. If None,
            the search will be performed in the root directory. Defaults to None.

    Returns:
        folder_id (str): The folder ID of the specified folder.
    """

    # 1. Check whether Progress Report folder exists or not
    folders = search_folder(folder_name, parent_id)

    # if folder is not present then create the directory
    if not folders:
        # 2. if directory is not present then create the directory
        folder_id = create_folder(folder_name, parent_folder_id=parent_id)
    else:
        folder_id = folders[0]["id"]

    return folder_id


def filter_full_evaluated_assessments(assessment_df):
    """Filters and fetches assessment data from the 'evaluations_status' table to determine the number of learners
    who participated in an assessment, how many submitted their projects, and how many were evaluated.
    The 'is_evaluated' attribute in the evaluation status indicates whether a participant is evaluated or not.
    Total submitted participants and total evaluated participants are calculated.
    If the total submitted participants equal the total evaluated participants, their scores are reflected in
    the Google Sheet. This includes learners who may not have started or submitted their projects but are not counted
    under total participants.

    Only assessments where the total participants match the evaluated participants are considered in the output.
    Assessments where these counts differ are excluded.

    Args:
        assessment_df (DataFrame): The DataFrame containing project-wise scores of participants for processing.

    Returns:
        fully_evaluated_assessment (DataFrame): The DataFrame containing assessments where the total number of
        participants matches the number of participants evaluated.
    """

    ## Here we have to do the changes, that although learner haven't attempted the assessment
    ## his scores will be visible in the progress_report.
    ## In total_participants only those participants are considered who have submitted the project,
    ## so the script won't wait for the one who haven't submitted the project and once all the participants
    ## who have submitted the project will be evaluated, the scores of that assessment will be reflected
    ## in the sheet. And the learner's who haven't submitted the project will contain the 0.
    assessment_evaluations_status_df = (
        assessment_df[assessment_df["learner_status_by_order"] == "Submitted"]
        .groupby(
            "project_name"
        )  # project_name is used insted of order_id, as many times it happend that
        # same project is deployed separately for some learners due to some issues, but the name of the project
        # will be the same.
        # Scores (%) will contain null values in the case when participants haven't evaluated for the project,
        # thus in total_participants all the participants will count who have submitted the project by counting the
        # participant_email, while in evaluated particiants only those participants will be consider
        # who has evaluated the project, the participants who haven't started won't be counted
        # in total_participants, but his scores will be reflected in the progress_report
        .agg(
            total_participants=("participant_email", "count"),
            evaluated_participants=("Scores (%)", "count"),
        )
        .reset_index()
    )

    # There can be some assessment where there is no evaluation, in that case there will be blank value
    # so fill that with 0
    assessment_evaluations_status_df[
        "evaluated_participants"
    ] = assessment_evaluations_status_df["evaluated_participants"].fillna(0)

    # Now merge the assessment_scores_df, with project_evaluations_status_df
    assessment_scores_evaluations_status_df = assessment_evaluations_status_df.merge(
        assessment_df, on="project_name", how="inner"
    )

    ## The case when there are no scores for participants indicating that he haven't started the project
    ## or he haven't submitted the project. fill the blank values of Scores(%) attibute with 0
    assessment_scores_evaluations_status_df[
        "Scores (%)"
    ] = assessment_scores_evaluations_status_df["Scores (%)"].fillna(0)

    # Now only filter out those records where total_participants are equal to evaluated_participants
    full_evaluated_assessment = assessment_scores_evaluations_status_df[
        assessment_scores_evaluations_status_df["total_participants"]
        == assessment_scores_evaluations_status_df["evaluated_participants"]
    ].reset_index(drop=True)

    return full_evaluated_assessment


def separate_and_generate_progress_reports(fact_df, folder_id, file_name):
    """Separates Masterclass - Progress and Assessment - Progress records based on their intent.
    Then, it calls the 'generate_report' function from the 'gspread_functions.py' module to generate
    progress reports for both Masterclass - Progress and Assessment - Progress.

    Args:
        fact_df (DataFrame): The DataFrame containing both Masterclass and assessment records.
        folder_id (str): The ID of the folder where the progress report should be located.
        file_name (str): The name of the file where the progress and assessment reports should be reflected.
    """

    # learning df
    learning_df = fact_df[
        (fact_df["intent"] == "Learning") | (fact_df["intent"] == "Training")
    ]
    # Drop the scores (%)
    learning_df.drop("Scores (%)", axis=1, inplace=True)

    # Generate the Masterclass - Progress sheet
    generate_report(
        df=learning_df,
        sheet_name="Masterclass - Progress",
        value_column="Progress (%)",
        folder_id=folder_id,
        file_name=file_name,
    )

    # assessment df
    assessment_df = fact_df[fact_df["intent"] == "Assessment"]

    # Drop the "progress (%)"
    assessment_df.drop("Progress (%)", axis=1, inplace=True)

    # Filter only full evaluated assessment
    full_evaluated_assessment_df = filter_full_evaluated_assessments(assessment_df)

    # Generate the Assessment - Progress sheet
    generate_report(
        df=full_evaluated_assessment_df,
        sheet_name="Assessment - Progress",
        value_column="Scores (%)",
        folder_id=folder_id,
        file_name=file_name,
    )


def is_program_code(program_code):
    """A program code should contain both digit and numbers thus give that condition here

    1. We initialize two boolean variables, has_letters and has_digits, to False to keep track
    of whether we have encountered letters and digits in the string.
    2. We iterate through each character in the input string using a for loop.
    3. Inside the loop, we check each character using the char.isalpha() method to determine
    if it's a letter and char.isdigit() method to determine if it's a digit. If we encounter a letter,
    we set has_letters to True, and if we encounter a digit, we set has_digits to True.
    4. If both has_letters and has_digits become True, we return True because we've found both
    letters and digits in the string.
    5. If we complete the loop without finding both letters and digits, we return False.

    Args:
        program_code (str):
    """
    has_letters = False
    has_digits = False

    for char in program_code:
        if char.isalpha():
            has_letters = True
        elif char.isdigit():
            has_digits = True

        # If both letters and digits are found, no need to continue checking
        if has_letters and has_digits:
            return True

    return False


def main():
    """The main function where containing the main logic where the clients records, orders_dimensions
    records are calculated and then get_folder_id function is called to fetch the folder id if exists
    or create if not exists

    The client_id and client_name are mapped and then one by one all the scores of the programs will be
    filtered out based on the client_id that are currently running.

    The description of the projects are fetched from the 'orders_dimension' table, using the description
    program codes are fetched and check whether the program code is appropriate or not if yes then it
    continues and filter all the records based on that program_code.
    """

    # Fetch the folder_id of progress_report
    # This will search for folder, if folder is not there it will create the folder and return the id
    # else will directly return the id
    progress_report_folder_id = get_folder_id(PROGRESS_REPORT_FOLDER_NAME)

    cursor.execute("SELECT * FROM clients;")
    result = cursor.fetchall()
    column_names = [desc[0] for desc in cursor.description]

    client_df = pd.DataFrame(result, columns=column_names)

    client_ids = list(client_df["client_id"].unique())
    client_names = list(client_df["client_name"].unique())

    map_client_id_and_name = dict(zip(client_ids, client_names))

    # now fetch the orders_dimension details
    cursor.execute("SELECT * FROM orders_dimension;")

    result = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]

    orders_df = pd.DataFrame(result, columns=columns)

    # Now iterate through each client to create separate folder for that client
    for client_id, client_name in map_client_id_and_name.items():
        print(client_name)

        # Fetch the skills_fact calculation df
        skills_fact_calculation_df = skills_fact_calculation(
            cursor=cursor, client_id=client_id  # Tredence
        )

        # Now fetch the progress fact
        progress_fact_calculation_df = progress_fact_calculation(
            cursor=cursor, client_id=client_id
        )

        # Merge both skills_fact_calculation and progress_fact_calculation
        skills_fact_and_progress_fact = pd.merge(
            skills_fact_calculation_df,
            progress_fact_calculation_df.rename(
                {
                    "participants_name": "participant_name",
                    "participants_email": "participant_email",
                },
                axis=1,
            ),
            on=["participant_name", "participant_email", "order_id", "project_name"],
            how="right",
        )

        # fetch only those records that are active, this will exclude the cancelled orders
        active_orders = orders_df[orders_df["is_active"] == True]

        # merge the calculation of skills and progress fact with orders_dimension
        skills_fact_and_progress_fact_orders = pd.merge(
            skills_fact_and_progress_fact,
            active_orders.rename({"id": "order_id"}, axis=1),
            on="order_id",
            how="inner",
        )

        unique_description = list(
            skills_fact_and_progress_fact_orders["description"].unique()
        )

        # program_codes = (
        #     skills_fact_and_progress_fact_orders["description"]
        #     .apply(lambda desc: desc.split("_")[0])
        #     .unique()
        # )

        # Filter out only those program codes that contains _
        program_codes = set(
            [desc.split("_")[0] for desc in unique_description if "_" in desc]
        )

        if not program_codes:
            continue

        # Create the client_name directory such as Fractal, Tredence, etc or if it is
        # created then fetch the id of that.
        client_folder_id = get_folder_id(
            folder_name=client_name, parent_id=progress_report_folder_id
        )

        for program_code in program_codes:
            # if program code doesn't contain the digits and letters both, don't consider that program code
            if not is_program_code(program_code):
                continue

            # Once the client folder is created, now create the program_code folder or get id of that
            program_code_folder_id = get_folder_id(
                folder_name=program_code, parent_id=client_folder_id
            )

            # now filter out the data for this program code
            view_df = skills_fact_and_progress_fact_orders[
                skills_fact_and_progress_fact_orders["description"].str.contains(
                    program_code
                )
            ].reset_index(drop=True)

            view_df = view_df[
                [
                    "participant_name",
                    "participant_email",
                    "created_at",
                    "order_id",
                    "Scores (%)",
                    "Progress (%)",
                    "project_name",
                    "intent",
                    "learner_status_by_order",
                ]
            ]

            # The file_name should be pogram_code+"_"+report ex. GLOB-0567_report
            file_name = f"{program_code}_report"

            # it will take program code and based on that, it will filter the
            separate_and_generate_progress_reports(
                fact_df=view_df,
                folder_id=program_code_folder_id,  # the folder where the reports sheet will be created that is Program code folder
                file_name=file_name,
            )

    # At the end close the cursor and conn
    close_cursor_and_conn()


def lambda_handler(event, context):
    # Run the main function
    main()
    return {"message": "Report successfully generated"}


if __name__ == "__main__":
    main()