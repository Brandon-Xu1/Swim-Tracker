import streamlit as st
import sqlite3
import pandas as pd

# Define the function to store results in the database
def store_results(results):
    # Connect to the SQLite database (or create it if it doesn't exist)
    conn = sqlite3.connect("swim_data.db")
    cursor = conn.cursor()

    # Create the table if it doesn't already exist
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Results (
        swimmer_name TEXT,
        swimmer_id TEXT,
        event_id TEXT,
        time TEXT
    )
    """)

    # Insert the parsed results into the database
    for result in results:
        cursor.execute("INSERT INTO Results (swimmer_name, swimmer_id, event_id, time) VALUES (?, ?, ?, ?)",
                       (result["swimmer_name"], result["swimmer_id"], result["event_id"], result["time"]))

    # Commit the changes and close the connection
    conn.commit()
    conn.close()

    print(f"Successfully stored {len(results)} results in the database.")

# File path as a variable
file_path = "/Users/brandonxu/Downloads/Meet_Results-2024_TAC_TITANS_Jingle_Bells_Meet-20Dec2024-001/TEST.cl2"

# Define your function to process the file
def process_tm_file(file_path):
    try:
        with open(file_path, "r") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return "File not found. Please check the path."
    except Exception as e:
        return f"An error occurred: {e}"

    results = []
    
    for line in lines:
        if line.startswith("D01"):  # Example for swimmer results
            swimmer_name = line[7:31].strip()
            swimmer_id = line[31:39].strip()
            event_id = line[43:47].strip()
            time = line[58:65].strip()
            results.append({"swimmer_name": swimmer_name, "swimmer_id": swimmer_id, "event_id": event_id, "time": time})
    
    if results:
        store_results(results)  # Function to store results in your database
        return f"Successfully processed and stored {len(results)} results."
    else:
        return "No swimmer results found in the file."

# Call the function with the variable
result_message = process_tm_file(file_path)
st.write(result_message)



# Function to fetch swimmer data from the database
def get_swimmer_data(swimmer_name):
    conn = sqlite3.connect("swim_data.db")
    cursor = conn.cursor()
    
    # Fetch results for the swimmer based on their name
    cursor.execute("SELECT swimmer_name, event_id, time FROM Results WHERE swimmer_name LIKE ?", 
                   ('%' + swimmer_name + '%',))
    
    # Fetch the results
    data = cursor.fetchall()
    
    # Convert to a DataFrame
    df = pd.DataFrame(data, columns=["Swimmer Name", "Event ID", "Time"])
    
    conn.close()
    return df

# Function to convert time to seconds
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

# Streamlit interface
st.title("Swimmer Results")

# Input box to search for a swimmer's name
swimmer_name_input = st.text_input("Enter swimmer's name:")

if swimmer_name_input:
    # Get swimmer data from the database
    swimmer_data = get_swimmer_data(swimmer_name_input)
    
    if swimmer_data.empty:
        st.write("No results found for this swimmer.")
    else:
        # Display the results in a table
        st.write(f"Results for {swimmer_name_input}:")
        st.dataframe(swimmer_data)
        
        # Convert time to seconds for plotting
        swimmer_data['Time (Seconds)'] = swimmer_data['Time'].apply(convert_to_seconds)
        
        # Filter out rows with invalid time formats
        valid_data = swimmer_data[swimmer_data['Time (Seconds)'].notna()]
        
        if not valid_data.empty:
            # Optionally, plot a graph for the swimmer's times (you can customize the graph)
            st.bar_chart(valid_data.set_index('Event ID')['Time (Seconds)'])
        else:
            st.write("No valid time data for plotting.")
else:
    st.write("Please enter a swimmer's name to search.")
