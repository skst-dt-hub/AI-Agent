# History Tree Chat

Streamlit UI for chatting with the Bedrock Knowledge Base setup from `history_tree.py`.

## Run

From the project root:

```powershell
python -m streamlit run app.py
```

## Notes

- `history_tree.py` is not modified by this app.
- Conversation memory is kept in the current browser session through Streamlit session state.
- Closing the browser session or pressing "대화 초기화" clears the conversation.
