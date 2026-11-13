# One command per demo. Extra arguments go through ARGS, e.g.
#   make sanity ARGS="--set decoder.max_model_len=16384"
#   make sanity REPLAY=results/sanity-20261113-170000.json
SHELL := /bin/bash
RUN = source scripts/env.sh && source .venv/bin/activate && python -m
REPLAY_ARG = $(if $(REPLAY),--replay $(REPLAY),)

.PHONY: setup check-gpu sanity demo-clue demo-pmv2 demo-speed demo-book

setup:
	bash scripts/setup.sh

check-gpu:
	@$(RUN) demos.check_gpu

sanity:
	@$(RUN) demos.sanity $(REPLAY_ARG) $(ARGS)

demo-clue:
	@$(RUN) demos.clue $(REPLAY_ARG) $(ARGS)

demo-pmv2:
	@$(RUN) demos.pmv2 $(REPLAY_ARG) $(ARGS)

demo-speed:
	@$(RUN) demos.speed $(REPLAY_ARG) $(ARGS)

demo-book:
	@$(RUN) demos.book $(REPLAY_ARG) $(ARGS)
