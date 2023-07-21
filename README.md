# [GrooveShop](https://github.com/vasilistotskas/grooveShop)
####   -    [Django](https://github.com/vasilistotskas/grooveShop/tree/master/src)
####   -    [Nuxt3](https://github.com/vasilistotskas/grooveShop/tree/master/storefrontUINodeNuxt/src)
####   -    [MediaStream](https://github.com/vasilistotskas/grooveShop/tree/master/mediaStream)

## DOCKER :
   ### DJANGO :
   #### Run django db migrations through docker compose :
   -     docker compose run backend sh -c "python manage.py makemigrations --noinput"

   #### Run django db migrate through docker compose :
   -     docker compose run backend sh -c "python manage.py migrate"

   #### Create superuser through docker compose :
   -     docker compose run backend sh -c "python manage.py createsuperuser"

   #### Run django collectstatic through docker compose :
   -     docker compose run backend sh -c "python manage.py collectstatic --noinput"

   #### Run django tests through docker compose :
   -     docker compose run backend sh -c "python manage.py test tests/"

   #### Run django tests with coverage and html through docker compose :
   -     docker compose run backend sh -c "coverage run --omit=*/migrations/*,*/management/*,*/manage.py,*/setup.py,*/asgi.py,*/wsgi.py --source='.' manage.py test tests/ && coverage report && coverage html"

   #### Run django coverage html through docker compose :
   -     docker compose run backend sh -c "coverage html"

   #### Seed database with fake data through docker compose :
   -     docker compose run backend sh -c "python manage.py populate_all"

   #### Run docker compose for specific yml file :
   -     docker compose -f <docker compose-file.yml> up -d --build

   #### Run docker commands through docker exec :
   -     docker exec -it <container_id> <command>

   ### Run specific shell command through docker exec :
   -     docker exec -it <container_id> sh -c "<command>"

   ### Run Locale Message generation through docker exec :
   -     docker exec -it <container_id> sh -c "django-admin makemessages -l <locale>"
   -     docker exec -it <container_id> sh -c "django-admin makemessages --all --ignore=env"

   ### Run Locale Message compilation through docker exec :
   -     docker exec -it <container_id> sh -c "django-admin compilemessages --ignore=env"


## PYTHON
  ### --- VERSION 3.11.0 ---
  ### Virtual Environment :
   -     Install virtualenv : pip install virtualenv
         Create virtual environment : virtualenv <env_name>
         (Case 1)Activate virtual environment : source <env_name>/bin/activate
         (Case 2)Activate virtual environment : <env_name>/scripts/activate
         Deactivate virtual environment : deactivate
         Install requirements : pip install -r requirements.txt
         Install requirements for specific environment : pip install -r requirements/<env_name>.txt

  ### Django :
  -     Install django : pip install django
        Create django project : django-admin startproject <project_name>
        Create django app : python manage.py startapp <app_name>
        Run django db migrations : python manage.py makemigrations
        Run django db migrate : python manage.py migrate
        Create superuser : python manage.py createsuperuser
        Run django collectstatic : python manage.py collectstatic
        Run django test : python manage.py test
        Run django shell : python manage.py shell
        Run django shell_plus : python manage.py shell_plus
        Run django dbshell : python manage.py dbshell
        Run django runserver : python manage.py runserver

  ### Lint :
  -     Step 1: cd src
  -     AVAILABLE COMMANDS :
        pre-commit run --all-files
        black .

  ### Poetry :
  -     Install poetry : curl -sSL https://install.python-poetry.org | python3 - OR (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
        Create poetry project : poetry new <project_name>
        Install dependencies : poetry install
        Update poetry lock file : poetry lock
        Add dependency : poetry add <dependency_name>
        Remove dependency : poetry remove <dependency_name>
        Update dependency : poetry update <dependency_name>
        Run shell : poetry shell
        Run script : poetry run <script_name>

  ### Strawberry :
  -     Install strawberry : pip install strawberry-graphql
        Create strawberry project : strawberry server
        Run strawberry server : strawberry server
        Run strawberry server for project schema : (src path) : strawberry server core.graphql.schema:schema

  ### pre-commit :
  -     pre-commit install
        git config --unset core.hooksPath

  ### Anaconda :
  -     Install anaconda : https://docs.anaconda.com/anaconda/install/
        Create conda environment : conda create --name <env_name> python=3.11.0
        Activate conda environment : conda activate <env_name>
        Deactivate conda environment : conda deactivate
        Create conda environment from yml file : conda env create -f environment.yml

  ### DRF-Spectacular :
  -     Generate schema : python manage.py spectacular --color --file schema.yml


## MEDIA STREAM:
  ### NPM :
   ### --- VERSION 18.16.0 ---
   -     Step 1: cd mediaStream
   -     Run npm Install : npm install


## GIT
  ### --- VERSION 2.36.0.windows.1 ---
   #### Delete origin tags :
   -     git tag -l | xargs -n 1 git push --delete origin
   #### Delete local tags :
   -     git tag -l | xargs git tag -d
