import streamlit as st
import os
import shutil
from langchain_community.vectorstores import Chroma
# from langchain_openai import OpenAIEmbeddings, ChatOpenAI # Remove or comment out
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI # Add these
from langchain.text_splitter import RecursiveCharacterTextSplitter, Language
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain.memory import ConversationBufferMemory
from langchain.chains import ConversationalRetrievalChain

# --- Configuration ---
DEFAULT_CODE_DIR = "./my-java-project"
VECTORSTORE_DIR = "./java_vectorstore_gemini" # Use a different dir for Gemini embeddings
# Google AI Model Names (check Google AI Studio for latest free tier models)
GEMINI_EMBEDDING_MODEL = "models/embedding-001"
GEMINI_LLM_MODEL = "gemini-2.5-pro-exp-03-25" # Or "gemini-1.5-flash" if available/preferred

# --- Helper Functions ---

@st.cache_resource(show_spinner="Loading and Indexing Java Code (Gemini)...")
def create_or_load_index(code_dir: str, vectorstore_path: str, force_reindex: bool = False):
    """Loads Java code, splits it, creates Gemini embeddings, and stores them in ChromaDB."""
    if os.path.exists(vectorstore_path) and not force_reindex:
        st.info(f"Loading existing vector store from {vectorstore_path}")
        # Use Google Embeddings for loading existing store
        embeddings = GoogleGenerativeAIEmbeddings(model=GEMINI_EMBEDDING_MODEL)
        vector_store = Chroma(persist_directory=vectorstore_path, embedding_function=embeddings)
        return vector_store
    elif os.path.exists(vectorstore_path) and force_reindex:
         st.info(f"Re-indexing: Removing existing vector store at {vectorstore_path}")
         shutil.rmtree(vectorstore_path) # Remove old index

    if not os.path.exists(code_dir):
        st.error(f"Error: Code directory '{code_dir}' not found.")
        return None

    st.info(f"Creating new vector store from code in {code_dir}")
    try:
        # Load .java files (same as before)
        loader = DirectoryLoader(
            code_dir,
            glob="**/*.java",
            loader_cls=TextLoader,
            show_progress=True,
            use_multithreading=True
        )
        documents = loader.load()

        if not documents:
            st.warning("No .java files found in the specified directory.")
            return None

        # Split documents (same as before)
        java_splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.JAVA, chunk_size=1500, chunk_overlap=150
        )
        split_docs = java_splitter.split_documents(documents)

        # Add metadata (same as before)
        for doc in split_docs:
            if 'source' in doc.metadata:
                doc.metadata['filename'] = os.path.basename(doc.metadata['source'])

        # --- Create Google AI Embeddings ---
        st.info(f"Creating embeddings using Google AI '{GEMINI_EMBEDDING_MODEL}' (this might take a while)...")
        try:
            # Ensure API Key is set for embedding creation if not already loaded
            if "GOOGLE_API_KEY" not in os.environ:
                 st.error("Google API Key not found. Please set it in the sidebar.")
                 return None
            embeddings = GoogleGenerativeAIEmbeddings(model=GEMINI_EMBEDDING_MODEL)
        except Exception as e:
             st.error(f"Failed to initialize Google Embeddings. Error: {e}. Make sure your API key is correct and has permissions.")
             return None
        # --- End Embedding Change ---

        # Create Chroma vector store and persist (same logic, different embedding function)
        vector_store = Chroma.from_documents(
            documents=split_docs,
            embedding=embeddings,
            persist_directory=vectorstore_path
        )
        vector_store.persist()
        st.success(f"Vector store created and saved to {vectorstore_path}")
        return vector_store

    except Exception as e:
        st.error(f"Error during indexing: {e}")
        return None

# --- Streamlit UI ---

st.set_page_config(layout="wide", page_title="Streamsets CodeLense")
st.title("💬 Streamsets CodeLense")
st.markdown("Ask questions about your Java codebase. The system will retrieve relevant code snippets and use Gemini to generate an answer.")

# --- Sidebar for Configuration ---
st.sidebar.header("Configuration")

# --- Google AI API Key Input ---
google_api_key = st.sidebar.text_input(
    "Google AI API Key",
    type="password",
    help="Get yours from Google AI Studio (previously MakerSuite)."
)
if google_api_key:
    os.environ["GOOGLE_API_KEY"] = "AIzaSyDCreBnk7MgG4W3CwgnrP1oXvOzkWoqETg"
elif "GOOGLE_API_KEY" in os.environ:
    # If already set in environment, use that.
    st.sidebar.info("Using Google AI API Key from environment variable.")
    google_api_key = os.environ["GOOGLE_API_KEY"] # Ensure it's available for checks
else:
     st.sidebar.warning("Please enter your Google AI API Key to use the app.")
     st.info("Please enter your Google AI API Key in the sidebar to proceed.")
     st.stop() # Stop execution if no key is provided
# --- End API Key Change ---


# Code Directory Input (same as before)
code_directory = st.sidebar.text_input(
    "Path to your Java Codebase",
    value=DEFAULT_CODE_DIR
)

# Re-index Button (same as before)
force_reindex = st.sidebar.button("Re-index Codebase", help="Click to delete the existing index and rebuild it from the source code.")

# --- Main Application Logic ---

# Load or create the vector store (uses the updated function)
vector_store = create_or_load_index(code_directory, VECTORSTORE_DIR, force_reindex)

if vector_store:
    # Create the retriever (same as before)
    retriever = vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 5}
    )

    # --- Initialize Google Gemini LLM ---
    try:
        llm = ChatGoogleGenerativeAI(
            model=GEMINI_LLM_MODEL,
            temperature=0.2,
            convert_system_message_to_human=True # Important for Gemini with system prompts
            # You might need to add safety_settings depending on your content/needs
            # safety_settings = ...
            )
    except Exception as e:
         st.error(f"Failed to initialize Google Gemini LLM. Error: {e}. Make sure your API key is correct.")
         st.stop()
    # --- End LLM Change ---


    # Define the prompt template (can often remain the same)
    prompt_template = """You are an AI assistant helping developers understand a Java codebase.
Use the following pieces of context (retrieved code snippets) to answer the question at the end.
If you don't know the answer based on the context provided, just say that you don't know, don't try to make up an answer.
If the context includes code, present the relevant code snippets clearly in your answer.
Mention the source file (filename) if it's available in the context metadata.

Context:
{context}

Question: {question}

Helpful Answer:"""
    QA_CHAIN_PROMPT = PromptTemplate(
        input_variables=["context", "question"],
        template=prompt_template,
    )

    # --- Conversational Chain Setup (same logic, uses the new llm) ---
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "chat_history" not in st.session_state:
         st.session_state.chat_history = []

    # Display chat messages from history (same as before)
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Create conversational chain (uses the new llm)
    conversational_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        combine_docs_chain_kwargs={"prompt": QA_CHAIN_PROMPT},
        return_source_documents=True,
    )

    # --- User Input and Interaction (mostly same logic) ---
    user_query = st.chat_input("Ask a question about the code...")

    if user_query:
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        with st.spinner("Gemini is thinking..."):
            try:
                # Pass the current question and the managed chat history
                result = conversational_chain({
                    "question": user_query,
                    "chat_history": st.session_state.chat_history
                })
                answer = result["answer"]
                source_documents = result.get("source_documents", [])

                st.session_state.messages.append({"role": "assistant", "content": answer})
                st.session_state.chat_history.append((user_query, answer))

                with st.chat_message("assistant"):
                    st.markdown(answer)
                    if source_documents:
                        with st.expander("See Relevant Code Snippets"):
                            for doc in source_documents:
                                filename = doc.metadata.get('filename', 'Unknown File')
                                content_snippet = doc.page_content[:500] + "..." if len(doc.page_content) > 500 else doc.page_content
                                st.code(content_snippet, language='java', line_numbers=False)
                                st.caption(f"Source: {filename}")
                                st.divider()

            except Exception as e:
                # Specific check for potential Google API blocking (can be refined)
                if "Candidate was blocked due to" in str(e) or "SAFETY" in str(e).upper():
                     error_message = f"The response was blocked by Google's safety filters. This might be due to the retrieved code snippets or the question itself. Try rephrasing or ask about a different part of the code.\n\nDetails: {e}"
                     st.error(error_message)
                     st.session_state.messages.append({"role": "assistant", "content": error_message})
                else:
                    st.error(f"An error occurred: {e}")
                    st.session_state.messages.append({"role": "assistant", "content": f"Sorry, an error occurred: {e}"})


else:
    st.warning("Vector store could not be loaded or created. Please check the code directory path and permissions, and ensure your Google API Key is set.")