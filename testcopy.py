import re
import openai
import os
import pandas as pd
import streamlit as st
import sqlite3
import logging
logging.basicConfig(level=logging.DEBUG)

openai.api_key = "***REMOVED***"


file_path = "Meet Results-2024 TAC TITANS Jingle Bells Meet-20Dec2024-001.cl2"

def reformat_name(name):
    # Split the name by ', ' (last, first format)
    parts = name.split(', ')
    
    if len(parts) == 2:
        last_name = parts[0]
        first_middle_name = parts[1]
        first_middle_parts = first_middle_name.split(' ')
        first_name = first_middle_parts[0]
        return f"{first_name} {last_name}"
    else:
        # If the name doesn't follow the "Last, First" format, return it as is
        return name

def process_tm_file(file_path):
    if not os.path.exists(file_path):
        return "File not found. Please check the path."

    try:
        with open(file_path, "r") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return "File not found. Please check the path."
    except Exception as e:
        return f"An error occurred: {e}"

    results = []

    # Define event mapping
    event_mapping = {
    "1001": {"event": "100-yard Free", "distance": "100 yards", "stroke": "Freestyle"},
    "1002": {"event": "100-yard Back", "distance": "100 yards", "stroke": "Backstroke"},
    "1003": {"event": "100-yard Breast", "distance": "100 yards", "stroke": "Breaststroke"},
    "1004": {"event": "100-yard Fly", "distance": "100 yards", "stroke": "Butterfly"},
    "1005": {"event": "100-yard IM", "distance": "100 yards", "stroke": "Individual Medley"},

    "2001": {"event": "200-yard Free", "distance": "200 yards", "stroke": "Freestyle"},
    "2002": {"event": "200-yard Back", "distance": "200 yards", "stroke": "Backstroke"},
    "2003": {"event": "200-yard Breaststroke", "distance": "200 yards", "stroke": "Breaststroke"},
    "2004": {"event": "200-yard Fly", "distance": "200 yards", "stroke": "Butterfly"},
    "2005": {"event": "200-yard IM", "distance": "200 yards", "stroke": "Individual Medley"},

    "501": {"event": "50-yard Free", "distance": "50 yards", "stroke": "Freestyle"},
    "502": {"event": "50-yard Back", "distance": "50 yards", "stroke": "Backstroke"},
    "503": {"event": "50-yard Breaststroke", "distance": "50 yards", "stroke": "Breaststroke"},
    "504": {"event": "50-yard Fly", "distance": "50 yards", "stroke": "Butterfly"},
    
    
    "4005": {"event": "400-yard IM", "distance": "400 yards", "stroke": "Individual Medley"},
    "5001": {"event": "500-yard Free", "distance": "500 yards", "stroke": "Freestyle"},
    # Add more mappings as necessary
}
    
    for line in lines:
            if line.startswith("D01"):
                # Parse swimmer data
                swimmer_name = reformat_name(line[7:31].strip())
                event_id = line[68:72].strip()
                time = line[89:96].strip()
                event_info = event_mapping.get(event_id, {"event": "Unknown", "distance": "Unknown", "stroke": "Unknown"})

                results.append({
                    "name": swimmer_name,
                    "event_id": event_id,
                    "time": time,
                    "event": event_info["event"],
                    "distance": event_info["distance"],
                    "stroke": event_info["stroke"],
                })


    
    if results:
        store_results(results)  # Function to store results in your database
        return f"Successfully processed and stored {len(results)} results."
    else:
        return "No swimmer results found in the file."

def store_results(results):
    logging.debug("Attempting to connect to the database...")

    try:
        print("Attempting to connect to the database...")  # Add this to track code flow
        # Connect to the SQLite database
        conn = sqlite3.connect("swim_data.db")
        if conn:
            print("Database connection established.")
        else:
            print("Failed to connect to the database.")
        cursor = conn.cursor()


        # Drop the table if it exists (to recreate with the correct schema)
        cursor.execute("DROP TABLE IF EXISTS Results")

        # Create the table with the correct columns
        cursor.execute("""
        CREATE TABLE Results (
            name TEXT,
            event_id TEXT,
            time TEXT,
            event TEXT,
            distance TEXT,
            stroke TEXT
        )
        """)

        cursor.execute("PRAGMA table_info(Results)")

        # Insert results into the table
        for result in results:
            cursor.execute("""
            INSERT INTO Results (name, event_id, time, event, distance, stroke)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                result["name"],
                result["event_id"],
                result["time"],
                result["event"],
                result["distance"],
                result["stroke"]
            ))

        # Commit changes and close connection
        conn.commit()

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()
        print("Database connection closed.")


def convert_to_seconds(time_str):
    try:
        # Handle different formats of time, such as MM:SS or SS
        time_parts = time_str.split(":")
        
        if len(time_parts) == 2:  # MM:SS format
            minutes = float(time_parts[0])
            seconds = float(time_parts[1])
            return minutes * 60 + seconds
        elif len(time_parts) == 1:  # Just SS format
            return float(time_parts[0])
        else:
            return None  # Invalid format
    except Exception as e:
        return None  # Return None if there's an error in parsing the time

# Function to fetch swimmer data from the database
def get_swimmer_data(name):
    conn = sqlite3.connect("swim_data.db")
    cursor = conn.cursor()
    
    # Fetch results for the swimmer based on their name
    cursor.execute("""
    SELECT name, event, distance, stroke, time
    FROM Results
    WHERE name LIKE ?
    """, ('%' + name + '%',))
    
    # Fetch the results
    data = cursor.fetchall()
    
    # Convert to a DataFrame
    df = pd.DataFrame(data, columns=["Name", "Event", "Distance", "Stroke", "Time"])
    
    conn.close()
    return df

# Streamlit interface
st.title("Swimmer Results")

# Input box to search for a swimmer's name
name_input = st.text_input("Enter swimmer's name:")

if file_path:
    process_tm_file(file_path)  # Call this function to process the file


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


def interpret_query(query):
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",  # Or use "gpt-4" if you have access
        messages=[
            {"role": "system", "content": "You are a helpful assistant that translates user queries into valid SQL queries for a SQLite database. "
                "Ensure SQL uses 'LIKE' for partial matches and is case-insensitive for everything like name, event and so on. Use the format: "
                "SELECT * FROM Results WHERE name LIKE '%...' AND event LIKE '%...'. and whatever other parts like stroke or distance."
                "When writing event instead of 100 yard free write 100-yard free. So add the dash"
                "instead of a period at the end the query must always end with a semicolon ';'"},
            {"role": "user", "content": query}
        ]
    )
    # Accessing the content correctly from the response
    sql_query = response.choices[0].message.content.strip()    
    
    st.write(f"Generated SQL Query: {sql_query}")  # Streamlit output
    return sql_query

# Function to fetch results based on an interpreted SQL query
def get_results_from_query(sql_query):
    conn = sqlite3.connect("swim_data.db")
    cursor = conn.cursor()

    try:
        cursor.execute(sql_query)
        data = cursor.fetchall()

        st.write(f"Data fetched from the database: {data}")


        if data:
            # Dynamically determine the number of columns
            column_count = len(data[0])
            columns = ["Name", "Event", "Distance", "Stroke", "Time"][:column_count]
            df = pd.DataFrame(data, columns=columns)
        else:
            df = pd.DataFrame()  # Empty DataFrame

    except sqlite3.Error as e:
        logging.error(f"SQLite error: {e}")
        df = pd.DataFrame()

    finally:
        conn.close()

    return df


# Streamlit interface
st.title("Swimmer Results with AI Assistance")

# User input box for natural language query
query_input = st.text_input("Ask a question about swimmer performance:")

if query_input:
    # Use ChatGPT to interpret the query and generate an SQL query
    sql_query = interpret_query(query_input)
    st.write(f"Interpreted SQL query: {sql_query}")
    
    # Fetch the results from the database using the generated query
    swimmer_data = get_results_from_query(sql_query)
    
    if swimmer_data.empty:
        st.write("No results found for your query.")
    else:
        st.write("Results based on your query:")
        st.dataframe(swimmer_data)


