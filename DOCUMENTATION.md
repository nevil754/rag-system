
# RAG AI System Top-Tier

## Docker Compose stack:
- fastapi: for input/output data of the platform.
- celery: for ingestion tasks, in this way when the user upload a document the workers work in background processing the docs.
- qdrant: vector database, for save on map 3d the the vector points (metadata pieces of the docs ingetionated).
- redis: for cache functionalities (short-term memory), in couples key-value when a query has been requested again, and the data has not been changed, redis return the value already saved internally, avoiding an useless processing.
- sqlserver: for save in persistent mode (long-term memory) the data processed. 1 tenant is equivalent to a isolated schema.
This platform runs on a server GPU (built on nvidia xxx TODO check w 'nvidia-smi') and on a server CPU, in each server runs
docker compose --profile infra up -d --build   //sul server1CPU
docker compose --profile gpu up -d --build     //sul server2GPU
Before that, runs 'cp .env.example .env' to create a file .env from the file .env.example given, and compile that.
And also make sure that the linux user has the permissions to create folders (necessary for create the volumes folder and the file folder for each tenant).

## Infrastructure
This platform is Multi-Tenant, it means that each isolated tenant is a 'space'(or 'office') isolated each other.
Exists only 1 Superadmin (set credentials in the .env file) created during the building, he can create & manage all spaces.
Each space has it's own users (admin/user/viewer), documents, chat AI and settings.

## AI & LLMs & AI techniques utilized
This platform uses 4 AI models: 
- dense search: intfloat/multilingual-e5-large, actually using 'cosine similarity' technique for comprehend synonimous and concept. It doesn't catch exact keywords.
- sparse search: Qdrant/bm25, to catch exact keywords, acronymous, and codes. It doesn't catch the meanings.
- re-ranking: BAAI/bge-reranker-v2-m3, actually using 'MMR' technique for penalizing reasults too similiar to one another, given the best chunks by dense search and sparse search, creates a ranking and ultimate returns only the best chunks. 
- LLM: llama3.2:latest, to generate the AI response in format ChatGPT like.

Note: if in .env file the environment isn't 'production', you need to manually download the models:
```
docker compose exec fastapi python3.11 -c "   #utilizza il python dentro container fastapi (li dentro il python lo hai chiamato proprio 'python3.11')
from fastembed import TextEmbedding, SparseTextEmbedding
from sentence_transformers import CrossEncoder  #better than from fastembed.rerank.cross_encoder import CrossEncoder (that use basic fastembed)
TextEmbedding('intfloat/multilingual-e5-large')  #good x  queries/docs in multiple languages (included the italian), old was BAAI/bge-m3
SparseTextEmbedding('Qdrant/bm25')  #good for english bc has '_en_', less performing on other languages, old was prithivida/Splade_PP_en_v1  
CrossEncoder('BAAI/bge-reranker-v2-m3')  #new, old a little bit dated was BAAI/bge-reranker-base    
print('ok')
"
``` 

This platform has also an AI Agentic Architecture in evolution phase, unlinked, not tested so much.
This AI Agentic Architecture enables web scraping across internet, extending the Chat AI's knowledge beyond the isolated tenant environment.


