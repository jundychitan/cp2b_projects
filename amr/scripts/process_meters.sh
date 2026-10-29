#!/bin/bash

# Script to process meter batch file and execute SCRIPT_NAME from meter config
# Extracts parameters from meter_file and constructs command with proper options
# Generates output CSV with meter data based on OBIS code mappings extracted from config
ROOT_DIR="/home/admin"
CONFIG_DIR="${ROOT_DIR}/projects/config_file"
METER_DIR="${ROOT_DIR}/projects/amr/meter/"
SCRIPT_DIR="${ROOT_DIR}/projects/amr/scripts"
METER_BIN_DIR="${ROOT_DIR}/projects/amr/scripts"
OUTPUT_BASE="/tmp/amr"
LOG_OUTPUT_BASE="/tmp/amr/log"
CSV_FILE="${ROOT_DIR}/projects/amr/upload/meter_batch.csv"
#LOCATION=$(grep "^location" "${CONFIG_DIR}/location.cfg" | cut -d'=' -f2 | tr -d ' \"')
LOCATION=`cat $CONFIG_DIR/location.cfg`
# Create required directories if they don't exist
# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_BASE"
mkdir -p "$OUTPUT_BASE/log"
mkdir -p "$OUTPUT_BASE/log/lp"
mkdir -p "$(dirname "$CSV_FILE")"

if [ -f $CONFIG_DIR/sync_server ]; then
    echo "Syncing time to server..."
    url=`cat $CONFIG_DIR/server_ip.txt`
    echo $url
    php -f $SCRIPT_DIR/sync_time.php $url
else
    echo "Skip time sync to server..."
fi

# Helper function to extract config value
extract_config_value() {
    local file="$1"
    local key="$2"
    grep "^$key" "$file" 2>/dev/null | cut -d'=' -f2 | sed 's/^[ \t]*//;s/[ \t]*$//' | tr -d '"' || echo ""
}

# Helper function to parse OBIS codes and extract values from CSV report
parse_obis_data() {
    local csv_file="$1"
    local obis_code="$2"
    
    # Look for the OBIS code in the CSV and return the corresponding value
    # Only trim leading/trailing whitespace and quotes, preserve internal spaces (e.g. datetime)
    grep "^$obis_code" "$csv_file" 2>/dev/null | cut -d',' -f3 | sed 's/^[ \t]*//;s/[ \t]*$//' | tr -d '"' || echo ""
}

# Helper function to parse key=value data from txt report (e.g. jemstar output)
parse_keyvalue_data() {
    local txt_file="$1"
    local key_name="$2"
    
    # Look for key=value in the txt file and return the value
    grep "^${key_name}=" "$txt_file" 2>/dev/null | head -1 | cut -d'=' -f2 | sed 's/^[ \t]*//;s/[ \t]*$//' || echo ""
}

# Helper function to determine script name from config filename
determine_script_name() {
    local config_file="$1"
    local basename=$(basename "$config_file" .cfg)
    
    # Check if the config filename contains a hint about meter type
    if [[ "$basename" == *"zmd402"* ]]; then
        echo "zmd402_dlms"
    elif [[ "$basename" == *"jemstar"* ]]; then
        echo "jemstar"
    elif [[ "$basename" == *"prometer"* ]]; then
        echo "prometer100_dlms"
    else
        # Default: look for a script in /projects/amr/meter matching the config name
        if [ -f "${METER_BIN_DIR}/${basename}" ]; then
            echo "$basename"
        elif [ -f "${METER_BIN_DIR}/${basename}_dlms" ]; then
            echo "${basename}_dlms"
        else
            # Fallback: return empty to indicate no script found
            echo ""
        fi
    fi
}

# Helper function to create meter data output file with OBIS params from config
create_meter_data_file() {
    local output_file="$1"
    local meter_serial="$2"
    local report_file="$3"
    local location="$4"
    local meter_config="$5"
    
    # Initialize data array
    declare -A data
    
    # Default/extracted values
    data[save_to_meter_data]=1
    data[location]="$location"
    
    # Extract OBIS parameter mappings from meter config file
    # These are dynamic and meter-specific
    
    # Operational config keys to skip (not field mappings)
    local skip_keys="script_name verbose config_file location serial_port baudrate optical load_profile use_tcp_serial remote_ip_address remote_port password username instantaneous"
    
    # Use location from meter config if global LOCATION is empty
    local cfg_location=$(grep "^location" "$meter_config" 2>/dev/null | cut -d'=' -f2 | sed 's/^[ \t]*//;s/[ \t]*$//' | tr -d '"')
    if [ -n "$cfg_location" ] && [ -z "$location" ]; then
        data[location]="$cfg_location"
    fi
    
    # Get all parameter names that have OBIS code or field name mappings in the config
    while IFS='=' read -r param_name obis_code; do
        # Skip empty lines and comments
        [[ -z "$param_name" || "$param_name" =~ ^[[:space:]]*# ]] && continue
        
        # Trim whitespace from both sides
        param_name=$(echo "$param_name" | xargs)
        obis_code=$(echo "$obis_code" | xargs | tr -d '"')
        
        # Skip operational config keys
        if echo " $skip_keys " | grep -q " $param_name "; then
            continue
        fi
        
        # Check if this looks like an OBIS code (contains dots and numbers)
        if [[ "$obis_code" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            # Extract the value from CSV using the OBIS code
            value=$(parse_obis_data "$report_file" "$obis_code")
            data[$param_name]="$value"
            echo "  Extracted $param_name ($obis_code): $value"
        elif [[ "$obis_code" =~ ^[A-Za-z] ]]; then
            # Plain field name (e.g. jemstar: Va, Meter_ID, Total_Watts)
            value=$(parse_keyvalue_data "$report_file" "$obis_code")
            data[$param_name]="$value"
            echo "  Extracted $param_name ($obis_code): $value"
        fi
    done < "$meter_config"
    
    # URL-encode space in datetime field: "2026-05-12 16:23:14" -> "2026-05-12%2016:23:14"
    if [ -n "${data[datetime]}" ]; then
        data[datetime]=$(echo "${data[datetime]}" | sed 's/ /%20/g')
    fi
    
    # Set default values for parameters that might not be in config
    # (but were extracted from CSV via OBIS codes)
    data[pf]=${data[pf]:-0.00}
    data[va]=${data[va]:-0.00}
    data[var]=${data[var]:-0.00}
    data[varh_neg]=${data[varh_neg]:-0.0000}
    data[varh_pos]=${data[varh_pos]:-0.0000}
    data[varh_net]=${data[varh_net]:-0.0000}
    data[varh_total]=${data[varh_total]:-0.0000}
    data[vah_total]=${data[vah_total]:-0.0000}
    data[max_rec_kw_dmd]=${data[max_rec_kw_dmd]:-0.000}
    data[max_rec_kw_dmd_time]=${data[max_rec_kw_dmd_time]:-20}
    data[max_del_kw_dmd]=${data[max_del_kw_dmd]:-0.000}
    data[max_del_kw_dmd_time]=${data[max_del_kw_dmd_time]:-20}
    data[max_pos_kvar_dmd]=${data[max_pos_kvar_dmd]:-0.000}
    data[max_pos_kvar_dmd_time]=${data[max_pos_kvar_dmd_time]:-20}
    data[max_neg_kvar_dmd]=${data[max_neg_kvar_dmd]:-0.000}
    data[max_neg_kvar_dmd_time]=${data[max_neg_kvar_dmd_time]:-20}
    data[vpha_a]=${data[vpha_a]:-0.000}
    data[vpha_b]=${data[vpha_b]:-0.000}
    data[vpha_c]=${data[vpha_c]:-0.000}
    data[ipha_a]=${data[ipha_a]:-0.000}
    data[ipha_b]=${data[ipha_b]:-0.000}
    data[ipha_c]=${data[ipha_c]:-0.000}
    data[soft_rev]=${data[soft_rev]:-2.30}
    
    # Build query string
    local query_string="save_to_meter_data=${data[save_to_meter_data]}"
    for key in "${!data[@]}"; do
        if [ "$key" != "save_to_meter_data" ]; then
            query_string+="&${key}=${data[$key]}"
        fi
    done
    
    # Ensure parent directory exists before writing
    mkdir -p "$(dirname "$output_file")"
    
    # Write to file
    echo "$query_string" > "$output_file"
}

# Check if meter batch CSV file exists
if [ ! -f "$CSV_FILE" ]; then
    echo "Error: CSV file not found at $CSV_FILE"
    exit 1
fi

   
    # Read the CSV file line by line
    while IFS= read -r line; do
        # Skip empty lines
        [ -z "$line" ] && continue
        
        parameter1=$(echo "$line" | cut -d',' -f1)
        parameter3=$(echo "$line" | cut -d',' -f3)
        # Extract the second field (comma-delimited) - this is the meter config filename
        filename=$(echo "$line" | cut -d',' -f2)
        

        # Trim whitespace
        filename=$(echo "$filename" | xargs)
        
        # Check if filename is empty
        if [ -z "$filename" ]; then
            echo "Warning: Empty filename in line: $line"
            continue
        fi
        
        # Construct full path to meter file
        meter_file="${METER_DIR}${filename}"
        
        # Check if meter file exists
        if [ ! -f "$meter_file" ]; then
            echo "Warning: Meter file not found at $meter_file"
            continue
        fi
        
        echo "Processing: $filename"
        
        # Extract required parameters from meter_file
        # Requirement 1: Extract script_name or determine it automatically
        SCRIPT_NAME=$(extract_config_value "$meter_file" "script_name")
        
        # If script_name not found in config, try to determine it from the config filename
        if [ -z "$SCRIPT_NAME" ]; then
            SCRIPT_NAME=$(determine_script_name "$meter_file")
        fi
        
        if [ -z "$SCRIPT_NAME" ]; then
            echo "  Warning: Cannot determine script_name for $filename, skipping"
            continue
        fi
        
        # Initialize command options
        CMD_OPTIONS=""

        # Requirement 2: Extract serial_port and use if use_tcp_serial = 0
        USE_TCP=$(extract_config_value "$meter_file" "use_tcp_serial")
        if [ -z $USE_TCP ]; then
            USE_TCP=0;
        fi
        SERIAL_PORT=$(extract_config_value "$meter_file" "serial_port")
        
        if [ "$USE_TCP" == "0" ] && [ -n "$SERIAL_PORT" ]; then
            echo "  Serial Port: $SERIAL_PORT"
            if lsof $SERIAL_PORT; then  
                echo "Skipping interval read. Serial port is currently in use."
                continue                
            fi
        fi        
        
        if [ "$SCRIPT_NAME" != "amr_gateway" ]; then
            
            if [ "$USE_TCP" == "0" ] && [ -n "$SERIAL_PORT" ]; then
                CMD_OPTIONS="$CMD_OPTIONS -s \"$SERIAL_PORT\""
            fi
            
            # Requirement 3 & 4: Extract TCP parameters if use_tcp_serial = 1
            if [ "$USE_TCP" == "1" ]; then
                REMOTE_IP=$(extract_config_value "$meter_file" "remote_ip_address")
                REMOTE_PORT=$(extract_config_value "$meter_file" "remote_port")
                
                if [ -n "$REMOTE_IP" ]; then
                    CMD_OPTIONS="$CMD_OPTIONS -t \"$REMOTE_IP\""
                    echo "  Remote IP: $REMOTE_IP"
                fi
                
                if [ -n "$REMOTE_PORT" ]; then
                    CMD_OPTIONS="$CMD_OPTIONS -p \"$REMOTE_PORT\""
                    echo "  Remote Port: $REMOTE_PORT"
                fi
            fi
            
            # Extract config_file for -c option (OBIS parameter config)
            CONFIG_FILE=$(extract_config_value "$meter_file" "config_file")
            if [ -n "$CONFIG_FILE" ]; then
                CMD_OPTIONS="$CMD_OPTIONS -c \"$CONFIG_FILE\""
                echo "  Config File: $CONFIG_FILE"
            fi
            
            # Requirement 5: Extract load_profile if /tmp/lp_read exists
            LP_ACTIVE=""
            if [ -f "/tmp/lp_read" ]; then
                LOAD_PROFILE=$(extract_config_value "$meter_file" "load_profile")
                if [ -n "$LOAD_PROFILE" ]; then
                    LP_NUM=$(echo $LOAD_PROFILE | cut -d'.' -f2 | sed 's/^0*//')
                    # Date range: previous day 00:05 to current day 00:00
                    LP_START=$(date -d 'yesterday' '+%Y-%m-%d')_00:01
                    LP_END=$(date '+%Y-%m-%d')_00:00
                    CMD_OPTIONS="$CMD_OPTIONS -${LP_NUM} ${LP_START},${LP_END}"
                    LP_ACTIVE=1
                    echo "  Load Profile: -${LP_NUM} ${LP_START},${LP_END}"
                fi
            fi
            
            # Requirement 6: Add verbose option if verbose = 1
            VERBOSE=$(extract_config_value "$meter_file" "verbose")
            if [ "$VERBOSE" == "1" ]; then
                CMD_OPTIONS="$CMD_OPTIONS -v"
                echo "  Verbose: enabled"
            fi

            # Extract username from meter config and pass as -u option
            USERNAME=$(extract_config_value "$meter_file" "username")
            if [ -n "$USERNAME" ]; then
                CMD_OPTIONS="$CMD_OPTIONS -u \"$USERNAME\""
                echo "  Username: $USERNAME"
            fi

            # Extract password from meter config and pass as -w option
            PASSWORD=$(extract_config_value "$meter_file" "password")
            if [ -n "$PASSWORD" ]; then
                CMD_OPTIONS="$CMD_OPTIONS -w \"$PASSWORD\""
                echo "  Password: set"
            fi

            # Disable instantaneous read for jemstar with -n if instantaneous != 1
            if [ "$SCRIPT_NAME" == "jemstar" ]; then
                INSTANTANEOUS=$(extract_config_value "$meter_file" "instantaneous")
                if [ "$INSTANTANEOUS" != "1" ]; then
                    CMD_OPTIONS="$CMD_OPTIONS -n"
                    echo "  Instantaneous read: disabled"
                fi
            fi

            # Requirement 7: Add RS485/Optical option optical = 0 is RS485 mode
            # Note: jemstar does not support -r option
            if [ "$SCRIPT_NAME" != "jemstar" ]; then
                OPTICAL=$(extract_config_value "$meter_file" "optical")
                if [ "$OPTICAL" == "0" ]; then
                    CMD_OPTIONS="$CMD_OPTIONS -r"
                    echo "  RS485 Mode"
                fi
            fi
            
            # Requirement 8: Set output directory
            CMD_OPTIONS="$CMD_OPTIONS -o \"$OUTPUT_BASE\""

        else
            CMD_OPTIONS="$parameter1 $filename $parameter3 "
        fi
            
        # Determine the full path to the script - check both scripts and meter/bin directories
        SCRIPT_PATH=""
        if [ -f "$SCRIPT_DIR/$SCRIPT_NAME" ]; then
            SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_NAME"
        elif [ -f "$METER_BIN_DIR/$SCRIPT_NAME" ]; then
            SCRIPT_PATH="$METER_BIN_DIR/$SCRIPT_NAME"
        else
            echo "  Error: Script not found at $SCRIPT_DIR/$SCRIPT_NAME or $METER_BIN_DIR/$SCRIPT_NAME"
            continue
        fi
        
        # Execute the script with constructed options
        echo "  Executing: $SCRIPT_PATH $CMD_OPTIONS"
        eval "$SCRIPT_PATH $CMD_OPTIONS"
        
        if [ $? -eq 0 ]; then
            echo "  ✓ Successfully executed $SCRIPT_NAME"            
            if [ $SCRIPT_NAME != "amr_gateway" ]; then                
                # Move load profile CSVs to lp/ subdirectory if LP was requested
                if [ -n "$LP_ACTIVE" ]; then
                    LP_DIR="$LOG_OUTPUT_BASE/lp"
                    mkdir -p "$LP_DIR"
                    for csv_file in "$OUTPUT_BASE"/*.csv; do
                        [ -f "$csv_file" ] || continue
                        # Meter data CSVs have OBIS_Code header; LP CSVs do not
                        if ! head -1 "$csv_file" | grep -q "^OBIS_Code"; then
                            mv "$csv_file" "$LP_DIR/"
                            echo "  Moved LP file to: $LP_DIR/$(basename "$csv_file")"
                        fi
                    done
                fi
                
                # Requirement 7: Process auto-generated report and create meter data file
                # Look for the generated CSV file in output directory
                # File format: <Meter Serial_Number_Date_Time>.csv
                REPORT_FILES=$(ls "$OUTPUT_BASE"/*.csv "$OUTPUT_BASE"/*.txt 2>/dev/null)
                
                if [ -n "$REPORT_FILES" ]; then
                    for report_file in $REPORT_FILES; do
                        # Extract meter serial from report filename
                        basename_report=$(basename "$report_file")
                        # Remove extension (.csv or .txt)
                        meter_serial="${basename_report%.*}"
                        
                        # Create data file without extension
                        data_file="$LOG_OUTPUT_BASE/$meter_serial"
                        echo "  Creating meter data file: $data_file"
                        
                        # Pass the meter config file to create_meter_data_file
                        # so it can dynamically extract OBIS parameters
                        create_meter_data_file "$data_file" "$meter_serial" "$report_file" "$LOCATION" "$meter_file"
                        
                        # Delete the CSV report after processing
                        rm -f "$report_file"
                        echo "  Removed CSV report: $report_file"
                    done
                else
                    echo "  Warning: No CSV report file generated at $OUTPUT_BASE"
                fi
            fi
        else
            echo "  ✗ Error executing $SCRIPT_NAME"
        fi
        echo ""
    done < "$CSV_FILE"
    if [ -f "/tmp/lp_read" ]; then
        rm -f "/tmp/lp_read"
    fi
    echo "Processing complete."


