# ------------------------------------------
# Imports
# ------------------------------------------
import os
import sys
import argparse
# TODO: Add any additional imports your scoring logic requires (e.g. sklearn metrics, pandas, etc.)


# ------------------------------------------
# Scoring Class
# ------------------------------------------
class Scoring:
    """
    Class for computing competition scores.

    Attributes:
        * start_time (datetime): The start time of the scoring process.
        * end_time (datetime): The end time of the scoring process.
        * y_true: The ground-truth test labels.
        * y_pred: The model's predictions loaded from ingestion output.
        * scores_dict (dict): Dictionary of computed metric name -> value.
    """

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.y_true = None
        self.y_pred = None
        self.scores_dict = {}

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

    def load_reference_data(self, reference_dir):
        """
        Load ground-truth labels from reference_dir.
        """
        raise NotImplementedError("Fill in: load reference labels into self.y_true")

    def load_ingestion_result(self, predictions_dir):
        """
        Load model predictions produced by the ingestion step.
        """
        raise NotImplementedError("Fill in: self.y_pred = np.load(os.path.join(predictions_dir, 'predictions.npy'))")

    def compute_scores(self):
        """
        Compute the competition metric(s) and populate self.scores_dict.
        """
        raise NotImplementedError("Fill in: compute metric(s) and store in self.scores_dict")

    def write_scores(self, output_dir):
        """
        Write self.scores_dict to output_dir/scores.json.

        """
        raise NotImplementedError("Fill in: write self.scores_dict to scores.json in output_dir")


# ------------------------------------------
# Directories
# ------------------------------------------
module_dir = os.path.dirname(os.path.realpath(__file__))
root_dir_name = os.path.dirname(module_dir)

# ------------------------------------------
# Args
# ------------------------------------------
parser = argparse.ArgumentParser(
    description="Script to run the scoring program for the competition."
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
    print("Scoring Program started!")
    print("----------------------------------------------\n\n")

    args = parser.parse_args()

    if not args.codabench:
        # TODO: Adjust these folder names if your local bundle layout differs.
        prediction_dir = os.path.join(root_dir_name, "sample_result_submission")
        reference_dir = os.path.join(root_dir_name, "reference_data")
        output_dir = os.path.join(root_dir_name, "scoring_output")
    else:
        # DO NOT CHANGE THESE PATHS. THESE ARE USED ON THE CODABENCH PLATFORM.
        prediction_dir = "/app/input/res"
        reference_dir = "/app/input/ref"
        output_dir = "/app/output"

    sys.path.append(prediction_dir)
    sys.path.append(reference_dir)
    sys.path.append(output_dir)

    # Initialize scoring
    scoring = Scoring()

    # Start timer
    scoring.start_timer()

    # Load reference data
    scoring.load_reference_data(reference_dir)

    # Load ingestion result
    scoring.load_ingestion_result(prediction_dir)

    # Compute scores
    scoring.compute_scores()

    # Write scores
    scoring.write_scores(output_dir)

    # Stop timer
    scoring.stop_timer()

    print("\n---------------------------------")
    print(f"[✔] Total duration: {scoring.get_duration()}")
    print("---------------------------------")

    print("\n----------------------------------------------")
    print("[✔] Scoring Program executed successfully!")
    print("----------------------------------------------\n\n")
