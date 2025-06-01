'use client';

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import axios from '@/lib/axios'; // Assuming an axios instance is configured
import { GLAccount, GLAccountFormData } from '@/types/gl';
import { PlusCircle, Edit, Trash2 } from 'lucide-react';

// Mock/Placeholder components - replace with your actual UI library components
const Button = ({ onClick, children, className }: any) => <button className={`px-4 py-2 rounded ${className}`} onClick={onClick}>{children}</button>;
const Modal = ({ isOpen, onClose, title, children }: any) => isOpen ? <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center"><div className="bg-white p-6 rounded-lg shadow-xl w-1/3"><h2 className="text-xl font-semibold mb-4">{title}</h2>{children}<Button onClick={onClose} className="mt-4 bg-gray-200 hover:bg-gray-300 text-black">Close</Button></div></div> : null;

// API functions
const fetchGLAccounts = async (): Promise<GLAccount[]> => {
  const response = await axios.get('/gl/accounts');
  return response.data;
};

const createGLAccount = async (data: GLAccountFormData): Promise<GLAccount> => {
  const response = await axios.post('/gl/accounts', data);
  return response.data;
};

const updateGLAccount = async (id: number, data: GLAccountFormData): Promise<GLAccount> => {
  const response = await axios.put(`/gl/accounts/${id}`, data);
  return response.data;
};

const deleteGLAccount = async (id: number): Promise<void> => {
  await axios.delete(`/gl/accounts/${id}`);
};

export default function GLAccountsPage() {
  const queryClient = useQueryClient();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<GLAccount | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  const { data: accounts, isLoading, isError, error } = useQuery<GLAccount[], Error>({
    queryKey: ['glAccounts'],
    queryFn: fetchGLAccounts,
  });

  const createMutation = useMutation<GLAccount, Error, GLAccountFormData>({
    mutationFn: createGLAccount,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['glAccounts'] });
      setIsModalOpen(false);
      setEditingAccount(null);
    },
    onError: (error) => {
      console.error('Failed to create account:', error);
      // TODO: Show user-friendly error message
    },
  });

  const updateMutation = useMutation<GLAccount, Error, { id: number; data: GLAccountFormData }>({
    mutationFn: ({ id, data }) => updateGLAccount(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['glAccounts'] });
      setIsModalOpen(false);
      setEditingAccount(null);
    },
    onError: (error) => {
      console.error('Failed to update account:', error);
      // TODO: Show user-friendly error message
    },
  });

  const deleteMutation = useMutation<void, Error, number>({
    mutationFn: deleteGLAccount,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['glAccounts'] });
      setDeleteConfirmId(null);
    },
    onError: (error) => {
      console.error('Failed to delete account:', error);
      // TODO: Show user-friendly error message
    },
  });

  const handleAddAccount = () => {
    setEditingAccount(null);
    setIsModalOpen(true);
  };

  const handleEditAccount = (account: GLAccount) => {
    setEditingAccount(account);
    setIsModalOpen(true);
  };

  const handleSubmitForm = (formData: GLAccountFormData) => {
    if (editingAccount) {
      updateMutation.mutate({ id: editingAccount.id, data: formData });
    } else {
      createMutation.mutate(formData);
    }
  };

  const handleDeleteAccount = (id: number) => {
    setDeleteConfirmId(id);
  };

  const confirmDelete = () => {
    if (deleteConfirmId) {
      deleteMutation.mutate(deleteConfirmId);
    }
  };
  
  if (isLoading) return <p>Loading Chart of Accounts...</p>;
  if (isError) return <p>Error fetching accounts: {error?.message}</p>;

  return (
    <div className="container mx-auto p-4">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-semibold">Chart of Accounts</h1>
        <Button onClick={handleAddAccount} className="bg-primary text-white hover:bg-primary-dark">
          <PlusCircle className="inline-block mr-2 h-5 w-5" /> Add New Account
        </Button>
      </div>

      {/* TODO: Replace with a proper Table component */}
      <div className="bg-white shadow rounded-lg overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Code</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Balance</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Active</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {accounts?.map((account) => (
              <tr key={account.id}>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{account.account_code}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">{account.account_name}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{account.account_type}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 text-right">{account.current_balance}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {account.is_active ? 
                    <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">Active</span> : 
                    <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">Inactive</span>}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                  <Button onClick={() => handleEditAccount(account)} className="text-indigo-600 hover:text-indigo-900 mr-2"><Edit className="h-4 w-4" /></Button>
                  <Button onClick={() => handleDeleteAccount(account.id)} className="text-red-600 hover:text-red-900"><Trash2 className="h-4 w-4" /></Button>
                </td>
              </tr>
            ))}
            {accounts?.length === 0 && (
              <tr>
                <td colSpan={6} className="px-6 py-4 text-center text-sm text-gray-500">No accounts found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <AccountFormModal 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)} 
        onSubmit={handleSubmitForm} 
        initialData={editingAccount}
        allAccounts={accounts || []} 
      />

      {/* Delete Confirmation Modal */}
      <Modal 
        isOpen={deleteConfirmId !== null} 
        onClose={() => setDeleteConfirmId(null)} 
        title="Confirm Delete"
      >
        <p className="mb-4">Are you sure you want to delete this account? If the account has transactions, it will be marked as inactive instead.</p>
        <div className="flex justify-end space-x-3">
          <Button onClick={() => setDeleteConfirmId(null)} className="bg-gray-200 hover:bg-gray-300 text-black">
            Cancel
          </Button>
          <Button onClick={confirmDelete} className="bg-red-600 hover:bg-red-700 text-white">
            Delete
          </Button>
        </div>
      </Modal>
    </div>
  );
}

// Placeholder AccountFormModal - This should be a proper form component
interface AccountFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: GLAccountFormData) => void;
  initialData?: GLAccount | null;
  allAccounts: GLAccount[]; // For parent account selection
}

function AccountFormModal({ isOpen, onClose, onSubmit, initialData, allAccounts }: AccountFormModalProps) {
  const [formData, setFormData] = useState<GLAccountFormData>({
    account_code: '',
    account_name: '',
    account_type: 'EXPENSE',
    parent_account_id: null,
    is_active: true,
  });

  useEffect(() => {
    if (initialData) {
      setFormData({
        account_code: initialData.account_code,
        account_name: initialData.account_name,
        account_type: initialData.account_type,
        parent_account_id: initialData.parent_account_id,
        is_active: initialData.is_active,
      });
    } else {
      setFormData({
        account_code: '',
        account_name: '',
        account_type: 'EXPENSE', // Default for new account
        parent_account_id: null,
        is_active: true,
      });
    }
  }, [initialData, isOpen]); // Reset form when modal opens or initialData changes

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    const checked = (e.target as HTMLInputElement).checked;
    setFormData(prev => ({ ...prev, [name]: type === 'checkbox' ? checked : value }));
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const dataToSubmit: GLAccountFormData = {
        ...formData,
        parent_account_id: formData.parent_account_id ? Number(formData.parent_account_id) : null,
        is_active: formData.is_active === undefined ? true : formData.is_active, // Ensure boolean
    };
    onSubmit(dataToSubmit);
  };

  const accountTypes: GLAccount['account_type'][] = ['ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE'];

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={initialData ? "Edit GL Account" : "Add New GL Account"}>
      <form onSubmit={handleFormSubmit}>
        <div className="mb-4">
          <label htmlFor="account_code" className="block text-sm font-medium text-gray-700">Account Code</label>
          <input type="text" name="account_code" id="account_code" value={formData.account_code} onChange={handleChange} required 
                 className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm" />
        </div>
        <div className="mb-4">
          <label htmlFor="account_name" className="block text-sm font-medium text-gray-700">Account Name</label>
          <input type="text" name="account_name" id="account_name" value={formData.account_name} onChange={handleChange} required
                 className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm" />
        </div>
        <div className="mb-4">
          <label htmlFor="account_type" className="block text-sm font-medium text-gray-700">Account Type</label>
          <select name="account_type" id="account_type" value={formData.account_type} onChange={handleChange} required
                  className="mt-1 block w-full px-3 py-2 border border-gray-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm">
            {accountTypes.map(type => <option key={type} value={type}>{type}</option>)}
          </select>
        </div>
        <div className="mb-4">
          <label htmlFor="parent_account_id" className="block text-sm font-medium text-gray-700">Parent Account</label>
          <select name="parent_account_id" id="parent_account_id" value={formData.parent_account_id?.toString() || ''} onChange={handleChange}
                  className="mt-1 block w-full px-3 py-2 border border-gray-300 bg-white rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm">
            <option value="">None</option>
            {allAccounts.filter(acc => acc.id !== initialData?.id).map(acc => <option key={acc.id} value={acc.id}>{acc.account_code} - {acc.account_name}</option>)}
          </select>
        </div>
        <div className="mb-4">
          <div className="flex items-center">
            <input type="checkbox" name="is_active" id="is_active" checked={formData.is_active} onChange={handleChange} 
                   className="h-4 w-4 text-indigo-600 border-gray-300 rounded focus:ring-indigo-500"/>
            <label htmlFor="is_active" className="ml-2 block text-sm text-gray-900">Active</label>
          </div>
        </div>
        <div className="flex justify-end space-x-3">
            <Button type="button" onClick={onClose} className="bg-gray-200 hover:bg-gray-300 text-black">Cancel</Button>
            <Button type="submit" className="bg-primary hover:bg-primary-dark text-white">{initialData ? "Save Changes" : "Create Account"}</Button>
        </div>
      </form>
    </Modal>
  );
} 