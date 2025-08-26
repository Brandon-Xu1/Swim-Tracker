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

def parse_time_to_seconds(t: str) -> float:
    """
    Convert time strings to total seconds.
    Examples:
      "59.89"   -> 59.89 seconds
      "2:13.07" -> 133.07 seconds
    """
    t = t.strip()
    if ":" in t:
        minutes_str, seconds_str = t.split(":")
        return float(minutes_str) * 60 + float(seconds_str)
    else:
        return float(t)  

def parse_age_gender(code):  
    """
    code: something like '12MM' or '9FF'
    returns (age: int, gender: str) -> (12, 'M') or (9, 'F')
    """
    match = re.match(r"(\d+)(FF|MM)$", code.strip())
    if not match:
        return None, None
    age_str, gender_code = match.groups()
    age = int(age_str)
    gender = 'F' if 'F' in gender_code else 'M'
    return age, gender

def age_gender_to_group(age, gender):  # <-- ADDED
    """
    Convert an age (int) and gender ('F' or 'M') to a group label,
    e.g., 'Girls 11-12' or 'Boys 9-10'.
    Adjust logic per your actual age brackets.
    """
    if age is None or gender is None:
        return "Unknown"

    # Example bracket logic (modify as needed)
    if 9 <= age <= 10:
        bracket = "10 and under"
    elif 11 <= age <= 12:
        bracket = "11-12"
    elif 13 <= age <= 14:
        bracket = "13-14"
    elif 15 <= age <= 18:
        bracket = "15-18"
    else:
        bracket = str(age)  # fallback if no bracket

    label_gender = "Girls" if gender == 'F' else "Boys"
    return f"{label_gender} {bracket}"

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
    "1001": {"event": "100-yard Free"},
    "1002": {"event": "100-yard Back",  },
    "1003": {"event": "100-yard Breastroke",  },
    "1004": {"event": "100-yard Fly",},
    "1005": {"event": "100-yard IM",},

    "2001": {"event": "200-yard Free", },
    "2002": {"event": "200-yard Back",  },
    "2003": {"event": "200-yard Breastroke",  },
    "2004": {"event": "200-yard Fly",},
    "2005": {"event": "200-yard IM",},

    "501": {"event": "50-yard Free", },
    "502": {"event": "50-yard Back",  },
    "503": {"event": "50-yard Breastroke",  },
    "504": {"event": "50-yard Fly",},
    
    
    "4005": {"event": "400-yard IM",},
    "5001": {"event": "500-yard Free", },
    # Add more mappings as necessary
}
    
    for line in lines:
            if line.startswith("D01"):
                # Parse swimmer data
                swimmer_name = reformat_name(line[7:31].strip())
                code = line[63:67].strip()

                age, gender = parse_age_gender(code)  # <-- ADDED
                group_label = age_gender_to_group(age, gender)

                event_id = line[68:72].strip()
                time = line[89:96].strip()
                event_info = event_mapping.get(event_id, {"event": "Unknown"})

                results.append({
                    "name": swimmer_name,
                    "event_id": event_id,
                    "time": time,
                    "event": event_info["event"],
                    "group": group_label
                    

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
            group_label TEXT,
            event TEXT,
            time TEXT
            
            
        )
        """)

        cursor.execute("PRAGMA table_info(Results)")

        # Insert results into the table
        for result in results:
            cursor.execute("""
            INSERT INTO Results (name, group_label, event, time)
            VALUES (?, ?, ?, ?)
            """, (
                result["name"],
                result["group"],
                result["event"],
                result["time"]
                
                
                
            ))

        # Commit changes and close connection
        conn.commit()
        print(f"Inserted {len(results)} rows into the database.")
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
    SELECT name, group_label, event, time
    FROM Results
    WHERE name LIKE ?
    """, ('%' + name_input + '%'))
    
    # Fetch the results
    data = cursor.fetchall()
    
    # Convert to a DataFrame
    df = pd.DataFrame(data, columns=["Name", "Group", "Event", "Time"])
    
    conn.close()
    return df



if file_path:
    process_tm_file(file_path)  # Call this function to process the file



def interpret_query(query):
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # Or use "gpt-4" if you have access
        messages=[
            {"role": "system", "content": "You are a helpful assistant that translates user queries into valid SQL queries for a SQLite database. "
                "Ensure SQL uses 'LIKE' for partial matches and is case-insensitive for everything like name, event and so on. Use the format: "
                "SELECT * FROM Results WHERE name LIKE '%...' AND event LIKE '%...'. and so on"
                "When writing an event instead of 100 yard free write 100-yard free or 100-yard back for 100 yard back etc. So add the dash."
                "Always end query with semicolon ';'"
                "ORDER BY name"
                "if user inputs something that may require something i didn't explicitly mention (for example asking for a date), just ignore it and follow sql instructions stated before"
                 "When a user requests '100 yard times,' interpret that as any event containing '100-yard' in its name. "
                "Therefore, if a user says 'Natalie Xu 100 yard times,' generate a query like:\n"
                "SELECT * FROM Results WHERE name LIKE '%natalie xu%' AND event LIKE '%100-yard%';"
                "if user mentions gender or age, column should be group_label"
                "if user asks for times of a specific stroke without mentioning distance, show all events with that stroke separately. Ex: 'show me natalie xu's freestyle times', should return all events involving freestyle like 100-yard, 200-yard, etc"},
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

        # !!!!!!!!!!!!! st.write(f"Data fetched from the database: {data}")
        
        columns = ["Name", "Group", "Event", "Time"]
        df = pd.DataFrame(data, columns=columns)
        
        df["Time (Seconds)"] = df["Time"].apply(parse_time_to_seconds)
        df = df.sort_values("Time (Seconds)")


    except sqlite3.Error as e:
        logging.error(f"SQLite error: {e}")
        df = pd.DataFrame()

    finally:
        conn.close()

    return df


# Streamlit interface
st.title("R4S test 23")

# User input box for natural language query
query_input = st.text_input("Ask a question about swimmer performance:")

if query_input:
    # Use ChatGPT to interpret the query and generate an SQL query
    sql_query = interpret_query(query_input)
    st.write(f"Interpreted SQL query: {sql_query}")
    
    # Fetch the results from the database using the generated query
    swimmer_data = get_results_from_query(sql_query)
    
    if swimmer_data.empty:
        st.write("No results found")
    else:
        st.write("Results based on your query:")
        st.dataframe(swimmer_data, use_container_width=True, hide_index=True)


