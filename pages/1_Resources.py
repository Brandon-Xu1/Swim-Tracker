import re
import openai
import os
import pandas as pd
import streamlit as st
import sqlite3
import logging
logging.basicConfig(level=logging.DEBUG)

# Streamlit interface
st.title("Swimmer Results")

# Input box to search for a swimmer's name
name_input = st.text_input("Enter swimmer's name:")

if name_input:
    # Get swimmer data from the database
    swimmer_data = get_swimmer_data(name_input)
    
    if swimmer_data.empty:
        st.write("No results found for this swimmer.")
    else:
        # Display the results in a table
        st.write(f"Results for {name_input}:")
        st.dataframe(swimmer_data, use_container_width=True, hide_index=True)
        # Convert time to seconds for plotting
        swimmer_data['Time (Seconds)'] = swimmer_data['Time'].apply(convert_to_seconds)
        
        # Filter out rows with invalid time formats
        valid_data = swimmer_data[swimmer_data['Time (Seconds)'].notna()]
        
else:
    st.write("Please enter a swimmer's name to search.")