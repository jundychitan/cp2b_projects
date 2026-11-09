#!/bin/bash
mac=$(cat /sys/class/net/eth0/address)

# get server ip address
if [ -f /home/admin/projects/config_file/server_ip.txt ]; then
	server_ip=$(cat /home/admin/projects/config_file/server_ip.txt)
	
	# check if flagged for meter_batch.csv update
	check_csv=$(curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_update_csv 2>/dev/null)
	
	if [ -n "$check_csv" ]; then
		if [ "$check_csv" = "1" ]; then
			echo "update meter_batch.csv requested..."
			curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_content_csv >/home/admin/projects/amr/upload/meter_batch.csv
			exit_code=$?
			
			if [ $exit_code -eq 0 ]; then
				echo "successfully downloaded update..."
				curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/reset_update_csv
				chown www-data /home/admin/projects/amr/upload/meter_batch.csv
			else
				echo "failed to download update... exit code: $exit_code"
			fi
		fi

		# check if flagged for location update (using distinct variable)
		check_location=$(curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_update_location 2>/dev/null)
		
		if [ "$check_location" = "1" ]; then
			echo "update location requested..."
			curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_content_location >/home/admin/projects/config_file/location.cfg
			exit_code=$?
			
			if [ $exit_code -eq 0 ]; then
				echo "successfully downloaded update..."
				curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/reset_update_location
				chown www-data /home/admin/projects/config_file/location.cfg
			else
				echo "failed to download update... exit code: $exit_code"
			fi
		fi
	fi
fi

