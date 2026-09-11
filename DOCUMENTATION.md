
# RAG AI System Top-Tier

## Docker Compose stack:
- fastapi: for input/output data of the platform.
- celery: for ingestion tasks, in this way when the user upload a document the workers work in background processing the docs.
- qdrant: vector database, for save on map 3d the the vector points (metadata pieces of the docs ingetionated).
- redis: for cache functionalities (short-term memory), in couples key-value when a query has been requested again, and the data has not been changed, redis return the value already saved internally, avoiding an useless processing.
- sqlserver: for save in persistent mode (long-term memory) the data processed. 1 tenant is equivalent to a isolated schema.

This platform runs on a server GPU (built on nvidia xxx TODO check w 'nvidia-smi') and on a server CPU, in each server runs
docker compose --profile infra up -d --build   //on server1CPU
docker compose --profile gpu up -d --build     //on server2GPU
Before that, runs 'cp .env.example .env' to create a file .env from the file .env.example given, and compile that.
And also make sure that the linux user has the permissions to create folders (necessary to create the volumes folder and the file folder for each tenant).

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

## Debug Tools

### initial superadmin credentials:
admin@platform.competesrl.it
Admin123456!

### useful commands and endpoints
docker logs --tail 200 rag-system-dev-fastapi-1
docker logs --tail 200 rag-system-dev-celery-worker-default-1

http://192.168.113.52:8090   //my frontend dev, http://192.168.113.52:8091/ x staging, http://192.168.113.52:8092/  x production
192.168.113.52
http://192.168.113.52:8000/docs   //swagger UI endpoints
http://192.168.113.52:8000   //fastapi
http://192.168.113.52:5555   //flower (admin, beaflwer93!, )
http://192.168.113.52:8080   //chainlit (admin, admin) ma intanto non lo uso piu
http://192.168.113.52:6333   //qdrant (http://192.168.113.52:6333/dashboard to check the status of your vector points)
http://192.168.113.52:6379   //redis
http://192.168.113.52:1433   //sqlserver
https://eu.smith.langchain.com/  //european langsmith for tracing answers & responses of the Chat AI, insert ur key in the .env file

### connection container sqlserver to SQL Server Management Studio (SSMS):
open ssh -L 14330:127.0.0.1:1433 deploy@192.168.113.52 (password 12345678)
open SSMS con credentials
127.0.0.1,14330 
Authenticazione di SQL Server
SA
serv95psw!
check Trust Server Certificate

### ⚠️ clean all the volumes data:
docker buildx prune -af 
docker image prune -af   //ok, it doesn't delete the builds builded that ain't running
set -a; source .env; set +a   //pass the env vars definited in the .env file to the linux powershell
echo "DATA_ROOT=$DATA_ROOT"    //ur root in the server filesystem
//empties the data, if you want to delete also the 3 ai models downloaded, add also 'fastembed-cache'
for d in sqlserver qdrant redis uploads; do
  docker run --rm -v "$DATA_ROOT/$d:/data" busybox sh -c "rm -rf /data/* /data/.[!.]*"
  chmod 777 "$DATA_ROOT/$d"
done
//check that the data are deleted, should return 8 (basic)
for d in sqlserver qdrant redis uploads ; do
  echo "=== $d ==="; ls -la "$DATA_ROOT/$d"
done



