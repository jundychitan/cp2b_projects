#!/bin/bash
mac=$(cat /sys/class/net/end0/address)
# get server ip address
if [ -f /projects/config_file/server_ip.txt ]; then
	server_ip=$(cat /projects/config_file/server_ip.txt)
	# check if flagged for meter_batch.csv update
	check_csv=$(curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_update_csv)
	if [ ! -z $check_csv ]; then
		if [ $check_csv -eq 1 ]; then
			echo "update meter_batch.csv requested..."
			curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_content_csv >/projects/amr/upload/meter_batch.csv
			if [ $? -eq 0 ]; then
				echo "successfully downloaded update..."
				curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/reset_update_csv
				chown www-data /projects/amr/upload/meter_batch.csv
			else
				echo "failed to download update... exit code: "
			fi
		fi

		# check if flagged for location update
		check_csv=$(curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_update_location)
		if [ $check_csv -eq 1 ]; then
			echo "update location requested..."
			curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/get_content_location >/projects/config_file/location.cfg
			if [ $? -eq 0 ]; then
				echo "successfully downloaded update..."
				curl --connect-timeout 30 http://$server_ip/rtu/index.php/rtu/rtu_check_update/$mac/reset_update_location
				chown www-data /projects/config_file/location.cfg
			else
				echo "failed to download update... exit code: "
			fi
		fi
	fi
fi 

