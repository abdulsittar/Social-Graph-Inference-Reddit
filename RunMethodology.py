import subprocess

scripts = [
    "1-RedditDataCollection.py",
    "2-DataPreprocessing.py",
    "3-InteractionExtraction.py"#,
    #"4-NetworkConstruction.py",
    #"5-AnalysisAndBenchmarking.py"
]

for script in scripts:
    print(f"⭐Running {script} ⭐")
    subprocess.run(["python", script], check=True)
    print(f"⭐ Finished {script} ⭐\n")

print("⭐ All scripts completed successfully! ⭐")
