<?php

$url=$argv[1];
$http_server='http://'.$url.'/check_time.php';
$ctx = stream_context_create(array('http'=>
    array(
        'timeout' => 5, //seconds
    )
));
$refno = @file_get_contents($http_server,false,$ctx);
//var_dump($refno);
if($refno==TRUE){
	$remote_serverTime= $refno;
	$local_time=date('Y-m-d H:i:s');
	$datetime1 = strtotime($local_time);
	echo $datetime1 ."\n";
	$datetime2 = strtotime($remote_serverTime);
	echo $datetime2 ."\n";
	$diff = abs($datetime1-$datetime2);
	echo "Time Difference: ".$diff."\n";
	if($diff>30){
		$cmd = 'timedatectl set-ntp 0';
		echo $cmd."\n";
		shell_exec($cmd);
		
		$cmd = 'timedatectl set-time "'.$remote_serverTime.'"';
		echo $cmd."\n";
		shell_exec($cmd);
		
		$cmd = 'timedatectl set-ntp 1';
		echo $cmd."\n";
		shell_exec($cmd);
	}
}else{
	echo "Failed to connect to server...\n";
}
