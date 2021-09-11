def get_score(pair, pair_list):
    return len(pair_list) - pair_list.index(pair)


pair_lists = []
for i in range(1, 4):
    with open(f"./bots_list{i}.txt") as f:
        pair_lists.append([x.strip() for x in f.readlines()])

scores = {}
for pair_list in pair_lists:
    for pair in pair_list:
        if pair not in scores:
            scores[pair] = get_score(pair, pair_list)
        else:
            scores[pair] += get_score(pair, pair_list)

scores = [(score, pair) for pair, score in scores.items()]
scores.sort(reverse=True)
for i, score in enumerate(scores):
    print(f"{i+1:2d} {score[0]:3d} {score[1]}")
