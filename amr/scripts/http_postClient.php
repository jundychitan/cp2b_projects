<?php

$save_to_meter_data=0;
$location="";
$meter_id="";
$datetime="";
$vrms_a="";
$vrms_b=0;
$vrms_c=0;
$irms_a=0;
$irms_b=0;
$irms_c=0;
$freq=0;
$pf=0;
$watt=0;
$va=0;
$var=0;
$wh_rec=0;
$wh_del=0;
$wh_net=0;
$wh_total=0;
$varh_neg=0;
$varh_pos=0;
$varh_net=0;
$varh_total=0;
$vah_total=0;
$max_rec_kw_dmd=0;
$max_rec_kw_dmd_time=0;
$max_del_kw_dmd=0;
$max_del_kw_dmd_time=0;
$max_pos_kvar_dmd=0;
$max_pos_kvar_dmd_time=0;
$max_neg_kvar_dmd=0;
$max_neg_kvar_dmd_time=0;
$vpha_a=0;
$vpha_b=0;
$vpha_c=0;
$ipha_a=0;
$ipha_b=0;
$ipha_c=0;
$soft_rev=0;

$fp = fopen('/tmp/php-commit.lock', 'c');
if (!flock($fp, LOCK_EX | LOCK_NB)) {
    exit;
}
#var_dump($argv);
switch ($argc){
	case 1:
		//echo "case 1\n";
		$url="dychitan.com";
		$dir="/var/txtalert";
		$check_time=0;
		break;
	case 2:
		//echo "case 2\n";
		$url=$argv[1];
		$dir="/var/txtalert";
		$check_time=0;
		break;	
	case 3:
		//echo "case 3\n";
		$url=$argv[1];
		$dir=$argv[2];
		$check_time=0;
		break;
	case 4:
		//echo "case 4\n";
		$url=$argv[1];
		$dir=$argv[2];
		$check_time=$argv[3];
		break;	
	default:
		//echo "default\n";
		$url=$argv[1];
		$dir=$argv[2];
		$check_time=$argv[3];		
}

// Get MAC address - compatible with older and newer Linux versions
$mac_addr = '';
// Try newer method first (ip command)
$ip_output = shell_exec('ip link show end0 2>/dev/null | grep link/ether');
if ($ip_output) {
    $parts = preg_split('/\s+/', trim($ip_output));
    if (isset($parts[1])) {
        $mac_addr = $parts[1];
    }
}
// Fallback to older method (ifconfig)
if (empty($mac_addr)) {
    $ifconfig_output = shell_exec('/sbin/ifconfig end0 2>/dev/null');
    if ($ifconfig_output) {
        // Try to match HWaddr format (older systems)
        if (preg_match('/HWaddr\s+([0-9A-Fa-f:]+)/', $ifconfig_output, $matches)) {
            $mac_addr = $matches[1];
        }
        // Try to match ether format (newer systems)
        elseif (preg_match('/ether\s+([0-9A-Fa-f:]+)/', $ifconfig_output, $matches)) {
            $mac_addr = $matches[1];
        }
    }
}
if(!is_readable($dir))return NULL;
$files = scandir($dir); // scan directory for files in ascending order
foreach ($files as $key => $value){
	if(!in_array($value,array(".",".."))){
		if (!is_dir($dir . DIRECTORY_SEPARATOR . $value)){ //skip if directory
			if ($value === '.gitkeep') {
				#echo $value.": this is not valid file\n";
        		continue;
    		}		    		
			$raw_meter_data=file_get_contents($dir . DIRECTORY_SEPARATOR . $value);
			$array_meter_data=explode("&", $raw_meter_data);
			$array_count=count($array_meter_data);
			#echo $value.": this is a valid file\narray count=$array_count\n";
			if($array_count>=20){
				for($i=0;$i<$array_count;$i++){
					$data=explode("=",$array_meter_data[$i]);
					$meter_data[$i]=$data[1];
					switch ($data[0]){
						case "save_to_meter_data":							
							$save_to_meter_data=$data[1];
							//echo $save_to_meter_data."\n";
							break;
						case "location":
							$location=$data[1];
							break;
						case "meter_id":							
							$meter_id=$data[1];
							break;
						case "datetime":							
							$datetime=$data[1];
							break;
						case "vrms_a":							
							$vrms_a=$data[1];
							break;
						case "vrms_b":							
							$vrms_b=$data[1];
							break;
						case "vrms_c":							
							$vrms_c=$data[1];
							break;
						case "irms_a":							
							$irms_a=$data[1];
							break;
						case "irms_b":							
							$irms_b=$data[1];
							break;
						case "irms_c":							
							$irms_c=$data[1];
							break;
						case "freq":							
							$freq=$data[1];
							break;
						case "pf":							
							$pf=$data[1];
							break;
						case "watt":							
							$watt=$data[1];
							break;
						case "va":							
							$va=$data[1];
							break;
						case "var":							
							$var=$data[1];
							break;
						case "wh_rec":							
							$wh_rec=$data[1];
							break;
						case "wh_del":							
							$wh_del=$data[1];
							break;
						case "wh_net":							
							$wh_net=$data[1];
							break;
						case "wh_total":							
							$wh_total=$data[1];
							break;
						case "varh_neg":							
							$varh_neg=$data[1];
							break;
						case "varh_pos":							
							$varh_pos=$data[1];
							break;
						case "varh_net":							
							$varh_net=$data[1];
							break;
						case "varh_total":							
							$varh_total=$data[1];
							break;
						case "vah_total":							
							$vah_total=$data[1];
							break;
						case "max_rec_kw_dmd":							
							$max_rec_kw_dmd=$data[1];
							break;
						case "max_rec_kw_dmd_time":							
							$max_rec_kw_dmd_time=$data[1];
							break;
						case "max_del_kw_dmd":							
							$max_del_kw_dmd=$data[1];
							break;
						case "max_del_kw_dmd_time":							
							$max_del_kw_dmd_time=$data[1];
							break;
						case "max_pos_kvar_dmd":							
							$max_pos_kvar_dmd=$data[1];
							break;
						case "max_pos_kvar_dmd_time":							
							$max_pos_kvar_dmd_time=$data[1];
							break;
						case "max_neg_kvar_dmd":							
							$max_neg_kvar_dmd=$data[1];
							break;
						case "max_neg_kvar_dmd_time":							
							$max_neg_kvar_dmd_time=$data[1];
							break;
						case "vpha_a":							
							$vpha_a=$data[1];
							break;
						case "vpha_b":							
							$vpha_b=$data[1];
							break;
						case "vpha_c":							
							$vpha_c=$data[1];
							break;
						case "ipha_a":							
							$ipha_a=$data[1];
							break;
						case "ipha_b":							
							$ipha_b=$data[1];
							break;
						case "ipha_c":							
							$ipha_c=$data[1];
							break;
						case "soft_rev":							
							$soft_rev=$data[1];
							break;
						default:
					}						
				}
				$options = array(
				  'http'=>array(
					'timeout' => 10,
					'method'=>"POST",
					'header'=>
					  "Accept-language: en\r\n".
					  "Content-type: application/x-www-form-urlencoded\r\n",
					'content'=>http_build_query(
						array(
							'save_to_meter_data'=>$save_to_meter_data,
							'location'=>$location,
							'meter_id'=>$meter_id,
							'datetime'=>$datetime,
							'vrms_a'=>$vrms_a,
							'vrms_b'=>$vrms_b,
							'vrms_c'=>$vrms_c,
							'irms_a'=>$irms_a,
							'irms_b'=>$irms_b,
							'irms_c'=>$irms_c,
							'freq'=>$freq,
							'pf'=>$pf,
							'watt'=>$watt,
							'va'=>$va,
							'var'=>$var,
							'wh_rec'=>$wh_rec,
							'wh_del'=>$wh_del,
							'wh_net'=>$wh_net,
							'wh_total'=>$wh_total,
							'varh_neg'=>$varh_neg,
							'varh_pos'=>$varh_pos,
							'varh_net'=>$varh_net,
							'varh_total'=>$varh_total,
							'vah_total'=>$vah_total,
							'max_rec_kw_dmd'=>$max_rec_kw_dmd,
							'max_rec_kw_dmd_time'=>$max_rec_kw_dmd_time,
							'max_del_kw_dmd'=>$max_del_kw_dmd,
							'max_del_kw_dmd_time'=>$max_del_kw_dmd_time,
							'max_pos_kvar_dmd'=>$max_pos_kvar_dmd,
							'max_pos_kvar_dmd_time'=>$max_pos_kvar_dmd_time,
							'max_neg_kvar_dmd'=>$max_neg_kvar_dmd,
							'max_neg_kvar_dmd_time'=>$max_neg_kvar_dmd_time,
							'v_ph_angle_a'=>$vpha_a,
							'v_ph_angle_b'=>$vpha_b,
							'v_ph_angle_c'=>$vpha_c,
							'i_ph_angle_a'=>$ipha_a,
							'i_ph_angle_b'=>$ipha_b,
							'i_ph_angle_c'=>$ipha_c,
							'soft_rev'=>$soft_rev,
							'mac_address'=>$mac_addr
						),'','&'
					)
				));
				
				//if ((filter_var($url, FILTER_VALIDATE_IP)) || (filter_var($_url, FILTER_VALIDATE_URL))) {
				if(strlen($url)>1){
					#echo "here...\n";
					$http_server='http://'.$url.'/http_post_server.php';
					$context = stream_context_create($options);
					$refno = @file_get_contents($http_server,false,$context);
					if($refno==TRUE){
						$response=explode(",",$refno);
						#echo $response[0]."\n";
						if(trim($response[0])=='OK'){
							unlink($dir . DIRECTORY_SEPARATOR . $value);
							date_default_timezone_set('Asia/Manila');
							$local_time=date('Y-m-d H:i:s');
							$remote_serverTime=trim($response[1]);
							echo "Gateway Time: ".$local_time."\n";
							echo "Server Time : ".$remote_serverTime."\n";
						}
					}else{
						echo "Failed to connect to server...\n";
						if($check_time==0){
							$buffer_dir = "/projects/amr/meter/buffer";
							@copy($dir . DIRECTORY_SEPARATOR . $value, $buffer_dir . DIRECTORY_SEPARATOR . $value);
							unlink($dir . DIRECTORY_SEPARATOR . $value);
						}
					}
				}else{
					echo "no valid server ip..\n";
					unlink($dir . DIRECTORY_SEPARATOR . $value);
				}
			}else{
				#echo "not valid meter data";
				unlink($dir . DIRECTORY_SEPARATOR . $value);
/*				$buffer_dir = "/projects/amr/meter/buffer";
				@mkdir($buffer_dir, 0755, true);
				@copy($dir . DIRECTORY_SEPARATOR . $value, $buffer_dir . DIRECTORY_SEPARATOR . $value);
*/
			}
			
		}		
	}
}
flock($fp, LOCK_UN);
fclose($fp);
?>
