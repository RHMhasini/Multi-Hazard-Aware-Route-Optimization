
"""Analyze class imbalance in synthetic hazard labels."""

from data_loader import load_synthetic_data
from data_validation import validate_dataset

LABEL_COLUMNS = ["flood_sim", "landslide_sim"]


def analyze_class_balance(data):
    """Display class counts, percentages, and imbalance ratios."""

    print("\n=== CLASS BALANCE ANALYSIS ===")

    for label in LABEL_COLUMNS:
        counts = data[label].value_counts()
        total = len(data)

        negative = int(counts.get(0, 0))
        positive = int(counts.get(1, 0))

        negative_pct = negative / total * 100
        positive_pct = positive / total * 100

        print(f"\nHazard: {label}")
        print(f"Class 0: {negative} ({negative_pct:.2f}%)")
        print(f"Class 1: {positive} ({positive_pct:.2f}%)")

        if positive == 0:
            print("WARNING: No positive samples available.")
        else:
            ratio = negative / positive
            print(f"Negative-to-positive ratio: {ratio:.2f}:1")


if __name__ == "__main__":
    dataset = load_synthetic_data()
    validate_dataset(dataset)
    analyze_class_balance(dataset)
