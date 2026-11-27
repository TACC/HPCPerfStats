def parse_int_line(line):
  parts = line.split()
  if len(parts) < 2:
    return 0
  return int(parts[0]) + int(parts[1])


def main():
  total = 0
  for i in range(1000):
    total += parse_int_line(str(i) + " " + str(i + 1))
  print(total)


if __name__ == "__main__":
  main()
