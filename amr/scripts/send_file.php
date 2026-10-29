<?php

$url = $argv[1];
$src_dir = $argv[2];
$dest_dir = $argv[3];
$overwrite = $argv[4];
$buffer_lp = $argv[5];
$buffer_dir = "/projects/amr/meter/buffer/lp";

if(!is_readable($src_dir))return NULL;
$files = scandir($src_dir); // scan directory for files in ascending order
foreach ($files as $key => $value){
	if(!in_array($value,array(".",".."))){
		if (!is_dir($src_dir . DIRECTORY_SEPARATOR . $value)){ //skip if directory
			$filename = $value;
			$full_path_filename = $src_dir . DIRECTORY_SEPARATOR . $value;

			if ($filename === '.gitkeep') {
        		continue;
    		}

			$post = array(
				'dest_dir' => $dest_dir,
				'overwrite' => $overwrite,
				'file_contents'=> new CURLFile($full_path_filename)
			);
			$ch = curl_init();
			curl_setopt($ch, CURLOPT_URL,'http://'.$url.'/lp/receive_file_rev2.php');
			curl_setopt($ch, CURLOPT_POST,1);
			curl_setopt($ch, CURLOPT_POSTFIELDS, $post);
			curl_setopt($ch, CURLOPT_RETURNTRANSFER,1);
			$result=curl_exec ($ch);
			curl_close ($ch);
			
			// Parse JSON response from receive_file_rev2.php
			$response = json_decode($result, true);
			
			// Check if response is valid JSON with SUCCESS status
			if(is_array($response) && isset($response['status']) && $response['status'] === 'SUCCESS'){
				echo json_encode($response, JSON_PRETTY_PRINT) . "\n";
				unlink($full_path_filename);
			}else{
				// Handle failure - could be invalid JSON or FAILED status
				if(is_array($response)){
					echo json_encode($response, JSON_PRETTY_PRINT) . "\n";
				
					$error_message = isset($response['message']) ? $response['message'] : 'Unknown error from server';}else{
					echo "Failed to connect to server or invalid response...\n";
					$error_message = $result;
				}
				
				if($buffer_lp == 0){
					@copy($full_path_filename, $buffer_dir . DIRECTORY_SEPARATOR . $value);
					unlink($full_path_filename);
				}
			}
		}
	}
}

?>

