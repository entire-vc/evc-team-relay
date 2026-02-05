<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';

  type Props = AlertDialogPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Root {...restProps}>
  {@render children?.()}
</AlertDialogPrimitive.Root>
