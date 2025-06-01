'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { X } from 'lucide-react';
import { useApi } from '@/hooks/useApi';

const companySchema = z.object({
  name: z.string().min(1, 'Company name is required'),
  address: z.string().optional(),
  contact_info: z.object({
    phone: z.string().optional(),
    email: z.string().email().optional().or(z.literal('')),
    website: z.string().url().optional().or(z.literal('')),
  }).optional(),
});

type CompanyFormData = z.infer<typeof companySchema>;

interface CompanyFormProps {
  company?: any;
  onClose: () => void;
  onSuccess: () => void;
}

export function CompanyForm({ company, onClose, onSuccess }: CompanyFormProps) {
  const [isLoading, setIsLoading] = useState(false);
  const api = useApi();

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<CompanyFormData>({
    resolver: zodResolver(companySchema),
    defaultValues: {
      name: company?.name || '',
      address: company?.address || '',
      contact_info: {
        phone: company?.contact_info?.phone || '',
        email: company?.contact_info?.email || '',
        website: company?.contact_info?.website || '',
      },
    },
  });

  const onSubmit = async (data: CompanyFormData) => {
    try {
      setIsLoading(true);
      
      // Clean up empty strings in contact_info
      const cleanedData = {
        ...data,
        contact_info: {
          phone: data.contact_info?.phone || undefined,
          email: data.contact_info?.email || undefined,
          website: data.contact_info?.website || undefined,
        },
      };
      
      if (company) {
        // Update existing company
        await api.put(`/api/companies/${company.id}`, cleanedData);
      } else {
        // Create new company
        await api.post('/api/companies', cleanedData);
      }
      
      onSuccess();
    } catch (error: any) {
      console.error('Failed to save company:', error);
      alert(error.response?.data?.detail || 'Failed to save company');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">
            {company ? 'Edit Company' : 'Create Company'}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Company Name
            </label>
            <input
              type="text"
              {...register('name')}
              className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {errors.name && (
              <p className="mt-1 text-sm text-red-600">{errors.name.message}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Address
            </label>
            <textarea
              {...register('address')}
              rows={3}
              className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">Contact Information</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-600 mb-1">Phone</label>
                <input
                  type="text"
                  {...register('contact_info.phone')}
                  className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-600 mb-1">Email</label>
                <input
                  type="email"
                  {...register('contact_info.email')}
                  className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {errors.contact_info?.email && (
                  <p className="mt-1 text-sm text-red-600">{errors.contact_info.email.message}</p>
                )}
              </div>

              <div>
                <label className="block text-xs text-gray-600 mb-1">Website</label>
                <input
                  type="text"
                  {...register('contact_info.website')}
                  placeholder="https://example.com"
                  className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {errors.contact_info?.website && (
                  <p className="mt-1 text-sm text-red-600">{errors.contact_info.website.message}</p>
                )}
              </div>
            </div>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-700 bg-gray-200 rounded-lg hover:bg-gray-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {isLoading ? 'Saving...' : company ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
} 