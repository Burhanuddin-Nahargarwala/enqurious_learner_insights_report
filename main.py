# Import the required libraries
import json
import pandas as pd

from gspread_functions import search_folder, create_folder, generate_report
from db import conn, cursor, close_cursor_and_conn
from dateutil.relativedelta import relativedelta
from datetime import datetime
from common import PROGRESS_REPORT_FOLDER_NAME


def filter_within_proxy_period(df, months):
    df["within_proxy_period"] = df["created_at"].apply(
        lambda date_str: date_str.date()
        > (datetime.now().date() - relativedelta(months=months))
    )

    df = df[df["within_proxy_period"] == True].reset_index(drop=True)

    # Afer filter drop the column
    df.drop("within_proxy_period", axis=1, inplace=True)

    return df


def skills_fact_calculation(cursor, client_id):
    # now fetch the skills_fact table
    cursor.execute(
        f"SELECT * FROM skills_fact WHERE client_id='{client_id}' AND is_current=True;"
    )

    # Now fetch the result
    skills_fact_result = cursor.fetchall()
    skills_fact_columns = [desc[0] for desc in cursor.description]

    skills_fact_df = pd.DataFrame(skills_fact_result, columns=skills_fact_columns)

    # Filter the table based on proxy period
    skills_fact_df = filter_within_proxy_period(skills_fact_df, months=3)
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
    # now fetch the progress fact table
    cursor.execute(
        f"SELECT * FROM progress_fact WHERE client_id='{client_id}' AND is_current=True;"
    )

    # Now fetch the result
    progress_fact_result = cursor.fetchall()
    progress_fact_columns = [desc[0] for desc in cursor.description]

    progress_fact_df = pd.DataFrame(progress_fact_result, columns=progress_fact_columns)

    # Filter the table based on proxy period
    progress_fact_df = filter_within_proxy_period(progress_fact_df, months=3)
    # print("Progress fact created_at: ", progress_fact_df["created_at"].unique())

    # Now fetch the percentage of progress
    progress_fact_df_calculation = (
        progress_fact_df.groupby(
            ["participants_name", "participants_email", "order_id", "project_name"],
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
    cursor.execute("SELECT * FROM evaluations_status;")
    result = cursor.fetchall()
    column_names = [desc[0] for desc in cursor.description]

    evaluations_status_df = pd.DataFrame(result, columns=column_names)

    evaluations_status_df = evaluations_status_df[
        ["order_id", "participant_email", "is_evaluated"]
    ]

    # Now merge the given skills_fact df with the evaluations_df
    assessment_scores_df = assessment_df.merge(
        evaluations_status_df,
        on=["order_id", "participant_email"],
        how="left",
    )

    assessment_scores_df.to_csv("assessment_scores.csv", index=False)

    ## Here we have to do the changes, that although learner haven't attempted the assessment
    ## his scores should will be visible in the progress_report.
    assessment_evaluations_status_df = (
        assessment_scores_df.groupby("order_id")
        # is_evaluated comes in the case of only those participants who have submitted the project,
        # so if those particiants who haven't submitted or started the project, then he won't be counted
        # in total_participants, but his scores will be reflected in the progress_report
        .agg(total_participants=("is_evaluated", "count"))
        .reset_index()
        .merge(
            assessment_scores_df[assessment_scores_df["is_evaluated"] == True]
            .groupby("order_id")
            .agg(evaluated_participants=("order_id", "count"))
            .reset_index(),
            on="order_id",
            how="left",
        )
    )

    # There can be some assessment where there is no evaluation, in that case there will be blank value
    # so fill that with 0
    assessment_evaluations_status_df[
        "evaluated_participants"
    ] = assessment_evaluations_status_df["evaluated_participants"].fillna(0)

    # Now merge the assessment_scores_df, with project_evaluations_status_df
    assessment_scores_evaluations_status_df = assessment_evaluations_status_df.merge(
        assessment_scores_df, on="order_id", how="inner"
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


def generate_report_based_on_program_code(fact_df, folder_id, file_name):
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
        file_name=file_name
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
        program_code (_type_): _description_
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
                ]
            ]

            # The file_name should be pogram_code+"_"+report ex. GLOB-0567_report
            file_name = f"{program_code}_report"

            # it will take program code and based on that, it will filter the
            generate_report_based_on_program_code(
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
