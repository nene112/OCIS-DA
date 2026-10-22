"""Verify the legacy new[] return is released once after copying."""
import ctypes
import unittest
from HD_Roe import OcisMILPNet


class StringOwnershipTest(unittest.TestCase):
    def test_copy_then_release(self):
        client = OcisMILPNet.__new__(OcisMILPNet)
        client.obj = None
        buffer = ctypes.create_string_buffer(b'[1.25,2]')
        pointer = ctypes.addressof(buffer)
        released = []
        client._get_stepdata_sim = lambda obj, kind: pointer
        client._free_stepdata_string = released.append
        self.assertEqual(client.get_stepdata_sim('node_h'), '[1.25,2]')
        self.assertEqual(released, [pointer])

    def test_old_dll_without_deallocator(self):
        client = OcisMILPNet.__new__(OcisMILPNet)
        client.obj = None
        client._get_stepdata_sim = lambda obj, kind: b'[3]'
        client._free_stepdata_string = None
        self.assertEqual(client.get_stepdata_sim('node_h'), '[3]')


if __name__ == '__main__':
    unittest.main()
