import os
import openai
import tiktoken
from dotenv import load_dotenv
from threading import Lock
from llama_index import (SimpleDirectoryReader,
                         GPTVectorStoreIndex,
                         ServiceContext,
                         StorageContext,
                         load_index_from_storage,
                         set_global_service_context,
                         download_loader)
from llama_hub.github_repo import GithubRepositoryReader, GithubClient
from llama_index.callbacks import CallbackManager, TokenCountingHandler
from llama_index.llms import OpenAI
from base_prompt import CHAT_REFINE_PROMPT, CHAT_QA_PROMPT
from llama_index.evaluation import ResponseEvaluator

load_dotenv()
openai.api_key = os.environ["OPENAI_API_KEY"]
GITHUB_API_KEY = os.environ["GITHUB_API_KEY"]
thread_lock = Lock()

# setup token counter
token_counter = TokenCountingHandler(tokenizer=tiktoken.encoding_for_model("gpt-3.5-turbo").encode)
callback_manager = CallbackManager([token_counter])
# define LLM
llm = OpenAI(temperature=0, model="gpt-3.5-turbo", streaming=False, max_tokens=1000)
service_context = ServiceContext.from_defaults(llm=llm, callback_manager=callback_manager)
set_global_service_context(service_context)


# builds new index from our data folder and GitHub repos
def build_index():
    global service_context

    combined_documents = []
    # load directory documents
    directory_document = SimpleDirectoryReader('./data', recursive=True).load_data()
    combined_documents += directory_document
    # load github documents
    download_loader("GithubRepositoryReader")
    github_client = GithubClient(GITHUB_API_KEY)
    owner = "pyth-network"
    repos = ["pyth-client-py", "pyth-client-js", "pyth-sdk-solidity", "pyth-sdk-rs", "documentation", "pyth-crosschain"]
    branch = "main"
    # build documents out of all repos
    for repo in repos:
        loader = GithubRepositoryReader(github_client,
                                        owner=owner,
                                        repo=repo,
                                        filter_directories=(["images"], GithubRepositoryReader.FilterType.EXCLUDE),
                                        verbose=False,
                                        concurrent_requests=10,
                                        )
        document = loader.load_data(branch=branch)
        combined_documents += document
    # build index
    index = GPTVectorStoreIndex.from_documents(combined_documents, service_context=service_context)
    # store index in ./storage
    index.storage_context.persist()


# used to add documents to existing stored index
def add_to_index():
    download_loader("GithubRepositoryReader")
    github_client = GithubClient(GITHUB_API_KEY)
    owner = "pyth-network"
    repos = ["pyth-serum", "publisher-utils", "solmeet-workshop-june-22", "oracle-sandbox", "pyth-sdk-js",
             "program-authority-escrow", "pyth-observer", "audit-reports", "example-publisher", "pyth-agent",
             "program-admin", "pyth-client", "pythnet", "governance"]
    branch = "main"

    combined_documents = []
    for repo in repos:
        loader = GithubRepositoryReader(github_client, owner=owner, repo=repo, filter_directories=(["images"], GithubRepositoryReader.FilterType.EXCLUDE), verbose=False, concurrent_requests=10)
        if repo == "governance":
            document = loader.load_data(branch="master")
        elif repo == "pythnet":
            document = loader.load_data(branch="pyth")
        else:
            document = loader.load_data(branch=branch)
        combined_documents += document

    storage_context = StorageContext.from_defaults(persist_dir="./storage")
    index = load_index_from_storage(storage_context)
    for doc in combined_documents:
        index.insert(doc)
    index.storage_context.persist()


def pyth_gpt(message):
    global service_context

    with thread_lock:
        # rebuild storage context
        storage_context = StorageContext.from_defaults(persist_dir="./storage")
        # load index
        index = load_index_from_storage(storage_context, service_context=service_context)
        # query the index
        query_engine = index.as_query_engine(text_qa_template=CHAT_QA_PROMPT,
                                             refine_template=CHAT_REFINE_PROMPT,
                                             similarity_top_k=3,
                                             streaming=False,
                                             service_context=service_context)
        # enter your prompt
        response = query_engine.query(message)
        # define evaluator
        evaluator = ResponseEvaluator(service_context=service_context)
        # evaluate if the response matches any source context (returns "YES"/"NO")
        eval_result = evaluator.evaluate(response)
        print("Response matches any source context: " + str(eval_result))
        # token counter
        print('Embedding Tokens: ', token_counter.total_embedding_token_count, '\n',
              'LLM Prompt Tokens: ', token_counter.prompt_llm_token_count, '\n',
              'LLM Completion Tokens: ', token_counter.completion_llm_token_count, '\n',
              'Total LLM Token Count: ', token_counter.total_llm_token_count, '\n')
        token_counter.reset_counts()

        return str(response)
