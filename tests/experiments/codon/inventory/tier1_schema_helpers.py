def schema_key_basename(token):
  if not token:
    return ""
  return token.split(",", 1)[0]


def compile_token(token):
  parts = token.split(",")
  width = 64
  for ele in parts[1:]:
    if ele.startswith("W="):
      width = int(ele[3:])
  return parts[0], width


def main():
  acc = 0
  for i in range(500):
    name, w = compile_token("user,W=" + str(40 + (i % 10)))
    acc += w + len(schema_key_basename(name))
  print(acc)


if __name__ == "__main__":
  main()
