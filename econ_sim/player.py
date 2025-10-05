import numpy as np 

class Player:
    def __init__(self) -> None:
        pass

    def recv_obs(self, obs):
        """obs: np.ndarray"""
        raise NotImplementedError
    
    def send_information(self,remain):
        """send information to matched pair"""
        raise NotImplementedError
    
    def recv_information(self,inform,remain):
        """recv information from matched pair"""
        raise NotImplementedError
    
    def make_decision(self):
        """make decision based on information"""
        raise NotImplementedError