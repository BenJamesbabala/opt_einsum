import numpy as np


def _compute_size(inds, ind_dict):
    # Computes the product of indices based on a dictionary
    ret = 1
    for i in inds:
        ret *= ind_dict[i]
    return ret


def _find_contraction(positions, input_sets, output_set):
    # Finds the contraction for a given set of input and output sets
    # positions - positions of the input_sets that are contracted
    # input_sets - list of sets in the input
    # output_set - output index set
    # returns:
    #   new_result - the indices of the resulting contraction
    #   remaining - list of sets that have not been contracted
    #   index_removed - indices removed from the entire contraction
    #   index_contract - the indices that are used in the contraction

    index_contract = set()
    index_remain = output_set.copy()
    remaining = []
    for ind, value in enumerate(input_sets):
        if ind in positions:
            index_contract |= value
        else:
            remaining.append(value)
            index_remain |= value

    new_result = index_remain & index_contract
    index_removed = (index_contract - new_result)
    remaining.append(new_result)
    return (new_result, remaining, index_removed, index_contract)


def _path_optimal(inp, out, ind_dict, memory):
    # Computes all possible ways to contract the tensors
    # inp - list of sets for input indices
    # out - set of output indices
    # ind_dict - dictionary for the size of each index
    # memory - largest allowed number of elements in a new array
    # returns path

    inp_set = map(set, inp)
    out_set = set(out)

    current = [(0, [], inp_set)]
    for iteration in range(len(inp) - 1):
        new = []
        # Grab all unique pairs
        comb_iter = zip(*np.triu_indices(len(inp) - iteration, 1))
        for curr in current:
            cost, positions, remaining = curr
            for con in comb_iter:

                contract = _find_contraction(con, remaining, out_set)
                new_result, new_inp, index_removed, index_contract = contract

                # Sieve the results based on memory, prevents unnecessarily large tensors
                if _compute_size(new_result, ind_dict) > memory:
                    continue

                # Find cost
                new_cost = _compute_size(index_contract, ind_dict)
                if len(index_removed) > 0:
                    new_cost *= 2

                # Build (total_cost, positions, indices_remaining)
                new_cost += cost
                new_pos = positions + [con]
                new.append((new_cost, new_pos, new_inp))

        # Update list to iterate over
        current = new

    # If we have not found anything return single einsum contraction
    if len(new) == 0:
        return [tuple(range(len(inp)))]

    new.sort()
    path = new[0][1]
    return path


def _path_opportunistic(inp, out, ind_dict, memory):
    # Finds best path by choosing the best pair contraction
    # Best pair is determined by the sorted tuple (-index_removed, cost)
    # inp - list of sets for input indices
    # out - set of output indices
    # ind_dict - dictionary for the size of each index
    # memory - largest allowed number of elements in a new array
    # returns path

    inp_set = map(set, inp)
    out_set = set(out)

    path = []
    for iteration in range(len(inp) - 1):
        iteration_results = []
        comb_iter = zip(*np.triu_indices(len(inp_set), 1))
        for positions in comb_iter:

            contract = _find_contraction(positions, inp_set, out_set)
            index_result, new_inp, index_removed, index_contract = contract

            # Sieve the results based on memory, prevents unnecessarily large tensors
            if _compute_size(index_result, ind_dict) > memory:
                continue

            # Build sort tuple
            removed_size = _compute_size(index_removed, ind_dict)
            cost = _compute_size(index_contract, ind_dict)
            sort = (-removed_size, cost)

            # Add contraction to possible choices
            iteration_results.append([sort, positions, new_inp])

        # If we did not find a new contraction contract remaining
        if len(iteration_results) == 0:
            path.append(tuple(range(len(inp) - iteration)))
            break

        # Sort based on first index
        iteration_results.sort()
        best = iteration_results[0]
        path.append(best[1])
        inp_set = best[2]

    return path


# Rewrite einsum to handle different cases
def contract(string, *views, **kwargs):
    """
    Attempts to contract tensors in an optimal order using both
    np.einsum and np.tensordot. Primarily aims at reducing the
    overall rank of the contration by building intermediates."

    Parameters
    ----------
    string : str
        Einsum string of contractions
    *view : list of views utilized
    debug : bool, (default: False)
        Level of printing.
    tensordot : bool, optional (default: True)
        If true use tensordot where possible.
    path : bool or list, optional (default: `opportunistic`)
        Choose the type of path.

        - if a list is given uses this as the path.
        - 'opportunistic' means a N^3 algorithm that opportunistically
            chooses the best algorithm.
        - 'optimal' means a N! algorithm that tries all possible ways of
            contracting the listed tensors.

    memory : int, optional (default: largest input or output array size)
        Maximum number of elements in an intermediate array.

    Returns
    -------
    output : ndarray
        The results based on Einstein summation convention.

    See Also
    --------
    einsum, tensordot, dot

    """

    # Split into output and input string
    if '->' in string:
        input_string, output_string = string.split('->')
    else:
        input_string = string
        # Build output string
        tmp_string = string.replace(',', '')
        output_string = ''
        for s in sorted(set(tmp_string)):
            if tmp_string.count(s) == 1:
                output_string += s

    # This can be fixed with an improved parsing function.
    if ('.' in input_string) or ('.' in output_string):
        raise ValueError("Ellipsis are not currenly supported in opt_einsum.")

    # Build a few useful list and sets
    input_list = input_string.split(',')
    input_set = map(set, input_list)
    output_set = set(output_string)
    indices = set(input_string.replace(',', ''))

    # TODO Should be cast up to double precision
    views = [np.asanyarray(v) for v in views]

    # Make sure number views is equivalent to the number of terms
    if len(input_list) != len(views):
        raise ValueError("Number of einsum terms must be equal to the number of views.")

    # Get length of each unique index and ensure all dimension are correct
    inds_left = indices.copy()
    dimension_dict = {}
    for tnum, term in enumerate(input_list):
        sh = views[tnum].shape
        if len(sh) != len(term):
            raise ValueError("Dimensions of array and term does not match for term %d.", tnum)
        for cnum, char in enumerate(term):
            dim = sh[cnum]
            if char in dimension_dict.keys():
                if dimension_dict[char] != dim:
                    raise ValueError("Size of label '%s' does not match other terms.", char)
            else:
                dimension_dict[char] = dim

    # Compute size of each input array plus the output array
    size_list = []
    for term in input_list + [output_string]:
        size_list.append(_compute_size(term, dimension_dict))
    out_size = max(size_list)

    # Grab a few kwargs
    debug_arg = kwargs.get("debug", False)
    tdot_arg = kwargs.get("tensordot", True)
    path_arg = kwargs.get("path", "opportunistic")
    memory_arg = kwargs.get("memory", out_size)
    return_path_arg = kwargs.get("return_path", False)

    # If total flops is very small just avoid the overhead altogether
    total_flops = _compute_size(indices, dimension_dict)
    if (total_flops < 1e6) and not return_path_arg:
        return np.einsum(string, *views)

    # If no rank reduction leave it to einsum
    if (indices == output_set) and not return_path_arg:
        return np.einsum(string, *views)

    if debug_arg:
        print('Complete contraction:  %s' % (input_string + '->' + output_string))
        print('       Naive scaling:%4d' % len(indices))

    # Compute path
    if not isinstance(path_arg, str):
        path = path_arg
    elif len(input_list) == 2:
        path = [(0, 1)]
    elif path_arg == "opportunistic":
        # Maximum memory is an important variable here, should be at most out_size
        memory_arg = min(memory_arg, out_size)
        path = _path_opportunistic(input_list, output_set, dimension_dict, memory_arg)
    elif path_arg == "optimal":
        path = _path_optimal(input_list, output_set, dimension_dict, memory_arg)
    else:
        raise KeyError("Path name %s not found", path_arg)

    # Return path if requested
    if return_path_arg:
        return path

    if debug_arg:
        print('-' * 80)
        print('%6s %6s %24s %40s' % ('scaling', 'GEMM', 'current', 'remaining'))
        print('-' * 80)

    # Start contraction loop
    for contract_inds in path:
        # Make sure we remove inds from right to left
        contract_inds = sorted(list(contract_inds), reverse=True)

        contract = _find_contraction(contract_inds, input_set, output_set)
        out_inds, input_set, index_removed, index_contract = contract

        # Build required structures and explicitly delete views
        # Make sure to loop from right to left
        tmp_views, tmp_input = [], []
        for x in contract_inds:
            tmp_views.append(views.pop(x))
            tmp_input.append(input_list.pop(x))

        # Consider doing tensordot
        tdot_result = tmp_input[0] + tmp_input[1]
        for s in index_removed:
            tdot_result = tdot_result.replace(s, '')
        can_tdot = tdot_arg & (len(tmp_views) == 2) & (len(index_removed) > 0)
        can_tdot &= (set(tmp_input[0]) ^ set(tmp_input[1])) == set(tdot_result)

        # Scalar product
        if ((len(tmp_input[0]) == 0) or (len(tmp_input[1]) == 0)) and (len(tmp_views) == 2):
            new_view = np.dot(tmp_views[0], tmp_views[1])
            index_result = tmp_input[0] + tmp_input[1]

        # Tensordot
        elif can_tdot:
            input_left = tmp_input[0]
            input_right = tmp_input[1]
            keep_left = set(input_left) - index_removed
            keep_right = set(input_right) - index_removed

            # Check for duplicate indices, cannot do einsum('iij,jkk->ik') operations here
            if (len(set(input_left)) != len(input_left)):
                new_inds = ''.join(keep_left) + ''.join(index_removed)
                tmp_views[0] = np.einsum(input_left + '->' + new_inds, tmp_views[0], order='C')
                input_left = new_inds

            if (len(set(input_right)) != len(input_right)):
                new_inds = ''.join(index_removed) + ''.join(keep_right)
                tmp_views[1] = np.einsum(input_right + '->' + new_inds, tmp_views[1], order='C')
                input_right = new_inds

            # Tensordot guarantees a copy for ndim > 2, should avoid skip if possible
            rs = len(index_removed)
            dim_left = _compute_size(keep_left, dimension_dict)
            dim_right = _compute_size(keep_right, dimension_dict)
            dim_removed = _compute_size(index_removed, dimension_dict)
            index_result = input_left + input_right
            for s in index_removed:
                index_result = index_result.replace(s, '')

            # This is ugly, but can vastly speed up certain operations
            # Vectordot
            if input_left == input_right:
                new_view = np.dot(tmp_views[0].ravel(), tmp_views[1].ravel())

            # Matrix multiply
            # No transpose needed
            elif input_left[-rs:] == input_right[:rs]:
                new_view = np.dot(tmp_views[0].reshape(dim_left, dim_removed),
                                  tmp_views[1].reshape(dim_removed, dim_right))

            # Transpose both
            elif input_left[:rs] == input_right[-rs:]:
                new_view = np.dot(tmp_views[0].reshape(dim_removed, dim_left).T,
                                  tmp_views[1].reshape(dim_right, dim_removed).T)

            # Transpose right
            elif input_left[-rs:] == input_right[-rs:]:
                new_view = np.dot(tmp_views[0].reshape(dim_left, dim_removed),
                                  tmp_views[1].reshape(dim_right, dim_removed).T)

            # Tranpose left
            elif input_left[:rs] == input_right[:rs]:
                new_view = np.dot(tmp_views[0].reshape(dim_removed, dim_left).T,
                                  tmp_views[1].reshape(dim_removed, dim_right))

            # Einsum is faster than vectordot if we have to copy
            elif (len(keep_left) == 0) or (len(keep_right) == 0):
                einsum_string = input_left + ',' + input_right + '->' + index_result
                new_view = np.einsum(einsum_string, tmp_views[0], tmp_views[1], order='C')

            # Conventional tensordot
            else:
                # Find indices to contract over
                left_pos, right_pos = (), ()
                for s in index_removed:
                    left_pos += (input_left.find(s),)
                    right_pos += (input_right.find(s),)
                new_view = np.tensordot(tmp_views[0], tmp_views[1], axes=(left_pos, right_pos))

            # Make sure the resulting shape is correct
            shape_result = tuple(dimension_dict[x] for x in index_result)
            if (new_view.shape != shape_result):
                if (len(index_result) > 0):
                    new_view = new_view.reshape(shape_result)
                else:
                    new_view = np.squeeze(new_view)

        # Conventional einsum
        else:
            # We can choose order of output indices, shortest first
            sort_result = [(dimension_dict[ind], ind) for ind in out_inds]
            index_result = ''.join([x[1] for x in sorted(sort_result)])
            einsum_string = ','.join(tmp_input) + '->' + index_result
            new_view = np.einsum(einsum_string, *tmp_views, order='C')

        # Print current contraction
        if debug_arg:
            einsum_string = ','.join(tmp_input) + '->' + index_result
            remaining = ','.join(input_list + [index_result]) + '->' + output_string
            print('%4d    %6s %24s %40s' % (len(index_contract), can_tdot, einsum_string, remaining))

        # Append new items
        views += [new_view]
        input_list += [index_result]
        del tmp_views, new_view  # Dereference what we can

    # We may need to do a final transpose
    if input_list[0] == output_string:
        return views[0]
    else:
        return np.einsum(input_list[0] + '->' + output_string, views[0], order='C').copy()


