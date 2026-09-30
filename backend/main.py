"""Point d'entree ASGI.

    uvicorn main:app --app-dir backend

Ce fichier ne contient QUE l'assemblage : toute la construction est dans
`app.create_app()`. Un point d'entree qui fait autre chose que ceci est un
point d'entree qu'on ne peut pas remplacer (tests, worker, tache planifiee).
"""
from app import create_app

app = create_app()
