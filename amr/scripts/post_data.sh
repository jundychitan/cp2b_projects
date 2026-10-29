#!/bin/bash

root_dir="/home/admin"
script_dir="${root_dir}/projects/amr/scripts"
project_dir="${root_dir}/projects/amr"
config_file_dir="${root_dir}/projects/config_file"
server="${config_file_dir}/server_ip.txt"
location_file="${config_file_dir}/location.cfg"
log_dir="/tmp/amr/log"
lp_dir="/tmp/amr/log/lp"
buffer_lp_dir="${root_dir}/projects/amr/meter/buffer/lp"
lp_block0_dir="${root_dir}/projects/amr/meter/buffer/lp/block0"
hist_dir="${root_dir}/projects/amr/meter/buffer/lp/hist"

buffer_log_dir="${root_dir}/projects/amr/meter/buffer"
amr_url=$(cat $server)
files=$log_dir
buffer_files=$buffer_log_dir
touch /tmp/post_data

defaults_dir=$project_dir/meter/defaults
while IFS= read -r line;
do
	param=$(echo $line |awk -F= '{print $1}' |tr -d " \"\t\r\n")
	value=$(echo $line |awk -F= '{print $2}' |tr -d " \"\t\r\n")
	#echo -e "*"$param"*"$value"*"
	if [ "$param" == "lp_dir" ]
	then	
		lp_dir=$value"/lp"
		lp_block0_dir=$value"/lp/block0"
		hist_dir=$value"/hist"	
	fi
	
	if [ "$param" == "log_dir" ];
	then
		log_dir=$value
	fi
	
	if [ "$param" == "location" ];
	then
		location=$value
	fi
done <$defaults_dir

# Check if the log directory exists
if [ ! -d "$log_dir" ]; then
    echo "Log directory $log_dir does not exist. Creating it..."
    
    # Create the parent directory if it doesn't exist
    mkdir -p "$(dirname "$log_dir")"  # This creates /tmp/amr if it doesn't exist
    mkdir -p "$log_dir"                # Now create /tmp/amr/log
    echo "Directory $log_dir created."
fi
mkdir -p "$lp_dir"
echo $log_dir
echo $lp_dir

location=$(cat $location_file)

if [ -f $server ]; then
	#send the data from buffer
	file_cnt=$(ls -A $files |wc -l)
	#echo $file_cnt
	if [ $file_cnt -gt 1 ]; then
		url=`cat $server`
		if [ -z "$url" ];then
			url="0"
		fi
		echo "Server   	:" $url
		echo "Meter Log Dir	:" $log_dir
		echo "LP Log Dir	:" $location
		php -f $script_dir/http_postClient.php $url $log_dir 0
	fi
	#send data from unsent buffer
	file_cnt=$(ls -A $buffer_files |wc -l)
	#echo $file_cnt
	if [ $file_cnt -gt 2 ]; then
		url=`cat $server`
		if [ -z "$url" ];then
			url="0"
		fi
		echo "Sending unsent files..."
		echo "Server   	:" $url
		echo "Meter Log Dir	:" $log_dir
		echo "LP Log Dir	:" $location
		php -f $script_dir/http_postClient.php $url $buffer_log_dir 1
	fi		

	running=0
	pidof amr_gateway
	instance=$?
	if [ $instance -eq 0 ]; then
		running=1
	else
		sleep 2
		pidof amr_gateway
		instance=$?
		if [ $instance -eq 0 ]; then
			running=1
		fi
	fi

	if [ $running -eq 0 ]; then #execute only if amr_gateway is not running
		#echo "amr_gateway not running"
		if [ -d $lp_dir ]; then
			if [ "$(find $lp_dir -type f)" ]; then		
				echo "Sending fresh LP..."			 
				php -f $project_dir/scripts/send_file.php $amr_url $lp_dir 'lp/'$location 1 0
				#php -f $project_dir/scripts/send_file.php $amr_url $lp_block0_dir 'lp/'$location 1
				#php -f $project_dir/scripts/send_file.php $amr_url $hist_dir 'history/'$location 1
				#$script_dir/upload_lp.sh $amr_url '/var/www/html/lp/upload/lp/'$location $lp_dir 
			fi
		fi
		if [ -d $buffer_lp_dir ]; then
			if [ "$(find $buffer_lp_dir -type f ! -name ".gitkeep")" ]; then
				echo "Sending unsent LP..."
				php -f $project_dir/scripts/send_file.php $amr_url $buffer_lp_dir 'lp/'$location 1 1
				#php -f $project_dir/scripts/send_file.php $amr_url $lp_block0_dir 'lp/'$location 1
				#php -f $project_dir/scripts/send_file.php $amr_url $hist_dir 'history/'$location 1
				#$script_dir/upload_lp.sh $amr_url '/var/www/html/lp/upload/lp/'$location $lp_dir 
			fi
		fi			
	fi
fi


