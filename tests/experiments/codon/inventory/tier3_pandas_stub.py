def main():
  try:
    import pandas as pd  # type: ignore[import-untyped]
  except ImportError:
    print("pandas_missing")
    return
  df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
  print(int(df["a"].sum()))


if __name__ == "__main__":
  main()
