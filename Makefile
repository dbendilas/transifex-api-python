test:
	tox

covtest:
	pytest --cov=. --cov-report=term-missing

debugtest:
	pytest -s

watchtest:
	pytest-watch
