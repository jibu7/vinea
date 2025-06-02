'use client';

import { useState, useEffect } from 'react';
import { Plus, Edit, Trash2, Building2, Search } from 'lucide-react';
import { useApi } from '@/hooks/useApi';
import { CompanyForm } from '@/components/forms/CompanyForm';

interface Company {
  id: number;
  name: string;
  address: string;
  contact_info: any;
  settings: any;
  created_at: string;
}

export default function CompaniesPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingCompany, setEditingCompany] = useState<Company | null>(null);
  
  const api = useApi();

  const fetchCompanies = async () => {
    try {
      setIsLoading(true);
      console.log('Fetching companies...');
      const response = await api.get('/api/companies');
      console.log('Companies response:', response.data);
      
      // Handle both paginated and direct array responses
      if (Array.isArray(response.data)) {
        setCompanies(response.data);
        setTotal(response.data.length);
        console.log('Loaded companies (array):', response.data.length);
      } else if (response.data.items) {
        setCompanies(response.data.items);
        setTotal(response.data.total);
        console.log('Loaded companies (paginated):', response.data.total);
      } else {
        console.error('Unexpected response format:', response.data);
        setCompanies([]);
        setTotal(0);
      }
    } catch (error) {
      console.error('Failed to fetch companies:', error);
      setCompanies([]);
      setTotal(0);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchCompanies();
  }, []);

  const handleDelete = async (companyId: number) => {
    if (!confirm('Are you sure you want to delete this company? This will delete all associated data.')) return;
    
    try {
      await api.delete(`/api/companies/${companyId}`);
      fetchCompanies();
    } catch (error) {
      console.error('Failed to delete company:', error);
    }
  };

  const filteredCompanies = companies.filter(company => 
    company.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    company.address?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold mb-2">Company Management</h1>
        <p className="text-gray-600">Manage companies and their settings</p>
      </div>

      <div className="bg-white rounded-lg shadow">
        <div className="p-4 border-b flex justify-between items-center">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 h-4 w-4" />
            <input
              type="text"
              placeholder="Search companies..."
              className="pl-10 pr-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add Company
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Company
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Address
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Contact Info
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Created
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-4 text-center text-gray-500">
                    Loading...
                  </td>
                </tr>
              ) : filteredCompanies.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-4 text-center text-gray-500">
                    No companies found
                  </td>
                </tr>
              ) : (
                filteredCompanies.map((company) => (
                  <tr key={company.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <Building2 className="h-5 w-5 text-gray-400 mr-2" />
                        <span className="text-sm font-medium text-gray-900">
                          {company.name}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {company.address || '-'}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {company.contact_info?.phone && (
                        <div>Phone: {company.contact_info.phone}</div>
                      )}
                      {company.contact_info?.email && (
                        <div>Email: {company.contact_info.email}</div>
                      )}
                      {!company.contact_info?.phone && !company.contact_info?.email && '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {new Date(company.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                      <button
                        onClick={() => {
                          console.log('Editing company:', company);
                          setEditingCompany(company);
                        }}
                        className="text-indigo-600 hover:text-indigo-900 mr-3"
                      >
                        <Edit className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(company.id)}
                        className="text-red-600 hover:text-red-900"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="px-6 py-3 border-t text-sm text-gray-500">
          Showing {filteredCompanies.length} of {total} companies
        </div>
      </div>

      {/* Create/Edit Company Modal */}
      {(showCreateModal || editingCompany) && (
        <CompanyForm
          company={editingCompany}
          onClose={() => {
            setShowCreateModal(false);
            setEditingCompany(null);
          }}
          onSuccess={() => {
            setShowCreateModal(false);
            setEditingCompany(null);
            fetchCompanies();
          }}
        />
      )}
    </div>
  );
} 