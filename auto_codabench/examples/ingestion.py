# ------------------------------------------
# Imports
# ------------------------------------------
import os
import sys
import argparse
# TODO: Add any additional imports your solution requires (e.g. pandas, PIL, torch, etc.)


# ------------------------------------------
# Ingestion Class
# ------------------------------------------
class Ingestion:
    """
    Class for handling the ingestion process.

    Attributes:
        * start_time (datetime): The start time of the ingestion process.
        * end_time (datetime): The end time of the ingestion process.
        * model (object): The model object.
        * ingestion_result (dict): The ingestion result dict.
    """

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.model = None
        self.ingestion_result = {}

    def start_timer(self):
        """
        Start the timer for the ingestion process.
        """
        self.start_time = dt.now()

    def stop_timer(self):
        """
        Stop the timer for the ingestion process.
        """
        self.end_time = dt.now()

    def get_duration(self):
        """
        Get the duration of the ingestion process.

        Returns:
            timedelta: The duration of the ingestion process.
        """
        if self.start_time is None:
            print("[-] Timer was never started. Returning None")
            return None

        if self.end_time is None:
            print("[-] Timer was never stopped. Returning None")
            return None

        return self.end_time - self.start_time

    def load_train_and_test_data(self, input_dir):
        """
        Load training and test data from input_dir.
        """
        raise NotImplementedError("Fill in: load train/test data from input_dir")

    def init_submission(self, Model):
        """
        Instantiate the submitted Model class.

        TODO: Set self.model = Model()
        """
        raise NotImplementedError("Fill in: self.model = Model()")

    def fit_submission(self):
        """
        Train the model on the loaded training data.

        TODO: Call self.model.fit(self.X_train, self.y_train)
        """
        raise NotImplementedError("Fill in: self.model.fit(self.X_train, self.y_train)")

    def predict_submission(self):
        """
        Generate predictions on the test data.

        TODO: Set self.y_pred = self.model.predict(self.X_test)
        """
        raise NotImplementedError("Fill in: self.y_pred = self.model.predict(self.X_test)")

    def save_predictions(self, output_dir):
        """
        Persist predictions to output_dir/predictions.npy.

        TODO: Use np.save to write self.y_pred to a file called predictions.npy
              inside output_dir. Print a confirmation message.
        """
        raise NotImplementedError("Fill in: np.save(os.path.join(output_dir, 'predictions.npy'), self.y_pred)")


# ------------------------------------------
# Directories
# ------------------------------------------
module_dir = os.path.dirname(os.path.realpath(__file__))
root_dir_name = os.path.dirname(module_dir)

# ------------------------------------------
# Args
# ------------------------------------------
parser = argparse.ArgumentParser(
    description="Script to run the ingestion program for the competition."
)
parser.add_argument(
    "--codabench",
    help="True when running on Codabench",
    action="store_true",
)

# ------------------------------------------
# Main
# ------------------------------------------
if __name__ == "__main__":

    print("\n----------------------------------------------")
    print("Ingestion Program started!")
    print("----------------------------------------------\n\n")

    args = parser.parse_args()

    if not args.codabench:
        # TODO: Adjust these folder names if your local bundle layout differs.
        input_dir = os.path.join(root_dir_name, "input_data")
        output_dir = os.path.join(root_dir_name, "sample_result_submission")
        program_dir = os.path.join(root_dir_name, "ingestion_program")
        submission_dir = os.path.join(root_dir_name, "sample_code_submission")
    else:
        # DO NOT CHANGE THESE PATHS. THESE ARE USED ON THE CODABENCH PLATFORM.
        input_dir = "/app/input_data"
        output_dir = "/app/output"
        program_dir = "/app/program"
        submission_dir = "/app/ingested_program"

    sys.path.append(input_dir)
    sys.path.append(output_dir)
    sys.path.append(program_dir)
    sys.path.append(submission_dir)

    # Import your Model class from the submission directory.
    from model import Model

    # Initialize ingestion
    ingestion = Ingestion()

    # Start timer
    ingestion.start_timer()

    # Load train and test data
    ingestion.load_train_and_test_data(input_dir)

    # Initialize submission
    ingestion.init_submission(Model)

    # Fit submission
    ingestion.fit_submission()

    # Predict submission
    ingestion.predict_submission()

    # Save predictions
    ingestion.save_predictions(output_dir)

    # Stop timer
    ingestion.stop_timer()

    print("\n------------------------------------")
    print(f"[✔] Total duration: {ingestion.get_duration()}")
    print("------------------------------------")

    print("\n----------------------------------------------")
    print("[✔] Ingestion Program executed successfully!")
    print("----------------------------------------------\n\n")
